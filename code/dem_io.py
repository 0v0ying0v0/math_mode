"""镇龙乡 30m DEM 读取（纯 Python，无 GDAL 依赖）。

GeoTIFF: WGS84(EPSG:4326), PackBits 压缩 Float32, 1486x1309, 1/3600 度像元。
栅格中心坐标范围: 东经 109.0328-109.4453, 北纬 22.8614-23.2247。
"""
import numpy as np, struct

DEM_PATH = ('数据/镇龙乡地理空间数据/镇龙乡及周边地理数据/'
            '数字高程模型数据（DEM）/镇龙乡及周边30米DEM.tif')
NODATA = -32767.0
LON0, LAT0 = 109.03277777777778, 23.224722222222223   # tiepoint: 左上像元中心
PIX = 1.0 / 3600.0                                     # 度/像元


def _packbits(buf, n):
    out = bytearray(); i = 0; L = len(buf)
    while i < L and len(out) < n + 200:
        h = buf[i]; i += 1
        if h < 128:
            out += buf[i:i + h + 1]; i += h + 1
        elif h > 128:
            if i < L:
                out += bytes([buf[i]]) * (257 - h); i += 1
    return bytes(out[:n])


def load_dem(path=DEM_PATH):
    """返回 (z[1309,1486] float32, lon[1486], lat[1309])。lon/lat 为像元中心坐标。"""
    d = open(path, 'rb').read()
    bo = '<' if d[:2] == b'II' else '>'
    ifd = struct.unpack(bo + 'I', d[4:8])[0]
    n = struct.unpack(bo + 'H', d[ifd:ifd + 2])[0]
    T = {}
    for i in range(n):
        e = ifd + 2 + 12 * i
        tag, typ, cnt = struct.unpack(bo + 'HHI', d[e:e + 8])
        T[tag] = (typ, cnt, e)
    def ivals(tag):
        typ, cnt, e = T[tag]
        if cnt == 1 and typ in (3, 4):
            return [struct.unpack(bo + ('H' if typ == 3 else 'I'), d[e + 8:e + 10 if typ == 3 else e + 12])[0]]
        off = struct.unpack(bo + 'I', d[e + 8:e + 12])[0]
        fmt = 'H' if typ == 3 else 'I'
        sz = 2 if typ == 3 else 4
        return list(struct.unpack(bo + str(cnt) + fmt, d[off:off + cnt * sz]))
    def dvals(tag):
        typ, cnt, e = T[tag]
        off = struct.unpack(bo + 'I', d[e + 8:e + 12])[0]
        return list(struct.unpack(bo + str(cnt) + 'd', d[off:off + cnt * 8]))
    W = ivals(256)[0]; H = ivals(257)[0]
    tw, th = ivals(322)[0], ivals(323)[0]        # TileWidth / TileLength
    offs, cnts = ivals(324), ivals(325)          # TileOffsets / TileByteCounts
    sx, sy, _ = dvals(33550)
    _, _, _, lon0, lat0, _ = dvals(33922)
    cols = -(-W // tw); rows = -(-H // th)
    if len(offs) != rows * cols:
        raise RuntimeError(f'tile count {len(offs)} != {rows}x{cols}')
    img = np.empty((rows * th, cols * tw), dtype=np.float64)
    need = tw * th * 4
    for k in range(len(offs)):
        r, c = divmod(k, cols)
        raw = _packbits(d[offs[k]:offs[k] + cnts[k]], need)
        if len(raw) < need:
            raise RuntimeError(f'tile {k} short: {len(raw)} < {need}')
        img[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = \
            np.frombuffer(raw[:need], dtype=bo + 'f4').reshape(th, tw)
    z = img[:H, :W]
    z[z <= NODATA + 1] = np.nan
    lon = lon0 + sx * np.arange(W)
    lat = lat0 - sy * np.arange(H)
    return z, lon, lat


def bilinear(z, lon, lat, qlon, qlat):
    """双线性插值采样高程；越界返回 nan。"""
    fc = (np.asarray(qlon, float) - lon[0]) / (lon[1] - lon[0])
    fr = (lat[0] - np.asarray(qlat, float)) / (lat[0] - lat[1])
    if np.any(fc < 0) or np.any(fc > lon.size - 1) or np.any(fr < 0) or np.any(fr > lat.size - 1):
        return np.nan
    c0 = np.floor(fc).astype(int); r0 = np.floor(fr).astype(int)
    c1 = np.minimum(c0 + 1, lon.size - 1); r1 = np.minimum(r0 + 1, lat.size - 1)
    tc = fc - c0; tr = fr - r0
    v = ((1 - tc) * (1 - tr) * z[r0, c0] + tc * (1 - tr) * z[r0, c1]
         + (1 - tc) * tr * z[r1, c0] + tc * tr * z[r1, c1])
    return float(v)


if __name__ == '__main__':
    z, lon, lat = load_dem()
    print('shape', z.shape, 'lon', lon[0], lon[-1], 'lat', lat[0], lat[-1])
    print('valid min/max %.1f %.1f' % (np.nanmin(z), np.nanmax(z)))
    print('nan count', int(np.isnan(z).sum()))
    for nm, lo, la in [('O01', 109.230852, 23.008509), ('S001', 109.243232, 23.033593),
                       ('S015', 109.192379, 23.049455)]:
        print(nm, "dem=%.1f" % bilinear(z, lon, lat, lo, la))
