# -*- coding: utf-8 -*-
"""2026 研究生数模 D 题 —— 公共物理规则层（唯一口径）

本模块是四问共用口径的唯一实现，任何求解脚本不得自行重写物理公式。
所有口径出处标注于 [附录2]/[附录3]/[附件]。

单位约定：距离 m，时间 s，质量 kg，能量 kWh，功率 kW，损耗 dB，频率 MHz。
"""
from __future__ import annotations
import os, math
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, '数据')

# ----------------------------------------------------------------------------
# 常量 [附录2] [附录3]
# ----------------------------------------------------------------------------
G = 9.8                 # 重力加速度 m/s^2（重力做功口径 E_up = m g h / eta_up）
CABIN = 30.0            # 服务区作业高度 = 地面海拔 + 30 m [附录2]
CLEARANCE = 50.0        # 计划巡航海拔 = 航段所经 DEM 像元最高地面高程 + 50 m [附录2]
KH = 1.0 / 3600.0       # 度/像元
DEG2M = 111320.0        # 纬度方向 1 度 ≈ 111320 m（经度方向乘 cos(lat)）
NODATA = -32767.0
F_MHZ = 2400.0          # 载波频率 [通信链路参数]
L_SYS = 3.0             # 系统损耗 dB
L_OBS = 10.0            # 地形遮挡附加损耗 dB
P_SENS = -98.0          # 接收灵敏度 dBm
M_FADE = 8.0            # 衰落裕量 dB
G01_H = 20.0            # 网关天线离地高度 m
RELAY_HMAX = 300.0      # 中继最大悬停离地高度 m [中继无人机数据]


# ----------------------------------------------------------------------------
# 数据装载 [附件]
# ----------------------------------------------------------------------------
def load_nodes():
    """返回 dict: code -> dict(name, lon, lat, elev, pop)。含 O01 与 S001..S015。"""
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '调度中心与服务区.xlsx')
    df = pd.read_excel(f, sheet_name='数据', header=None)
    out = {}
    o = df.iloc[2]
    out['O01'] = dict(name=o[1], lon=float(o[2]), lat=float(o[3]), elev=float(o[4]), pop=None)
    for i in range(6, 21):
        r = df.iloc[i]
        out[str(r[0])] = dict(name=r[1], lon=float(r[2]), lat=float(r[3]),
                              elev=float(r[4]), pop=float(r[5]))
    return out


def load_boxes():
    """80 个不可拆货箱。返回 DataFrame，保留原始列名。"""
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '物资需求与配送时限.xlsx')
    return pd.read_excel(f, sheet_name='逐箱货箱清单')


def load_demand():
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '物资需求与配送时限.xlsx')
    return pd.read_excel(f, sheet_name='数据')


def load_transport_types():
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '运输无人机数据.xlsx')
    df = pd.read_excel(f, sheet_name='数据', header=None)
    cols = list(df.iloc[1])
    out = {}
    for i in (2, 3, 4):
        r = df.iloc[i]
        out[str(r[0])] = {
            'name': r[1], 'm_empty': float(r[2]), 'q_max': float(r[3]),
            'vol_max': float(r[4]), 'v_cruise': float(r[5]),
            'L_empty': float(r[6]), 'L_full': float(r[7]),
            'E_use': float(r[8]), 'rho': float(r[9]) / 100.0,
            't_prep': float(r[10]), 't_box': float(r[11]),
            't_hand_base': float(r[12]), 't_hand_box': float(r[13]),
            'v_up': float(r[14]), 'v_down': float(r[15]),
            'eta_up': float(r[16]), 'eta_down': float(r[17]),
        }
    return out


def load_transport_fleet():
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '运输无人机数据.xlsx')
    df = pd.read_excel(f, sheet_name='数据', header=None)
    fleet = [(str(df.iloc[i][0]), str(df.iloc[i][1]), str(df.iloc[i][2])) for i in range(8, 16)]
    bat = {}
    for i in (19, 20, 21):
        bat[str(df.iloc[i][0])] = dict(count=int(df.iloc[i][1]), T_full=float(df.iloc[i][2]))
    return fleet, bat


def load_relay_types():
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '中继无人机数据.xlsx')
    df = pd.read_excel(f, sheet_name='数据', header=None)
    r = df.iloc[2]
    t = {
        'name': r[1], 'm_empty': float(r[2]), 'm_module': float(r[3]),
        'm_takeoff': float(r[4]), 'v_cruise': float(r[5]), 'P_cruise': float(r[6]),
        'E_use': float(r[7]), 'rho': float(r[8]) / 100.0,
        't_prep': float(r[9]), 't_link': float(r[10]), 't_turn': float(r[11]),
        'v_up': float(r[12]), 'v_down': float(r[13]),
        'eta_up': float(r[14]), 'eta_down': float(r[15]),
        'P_hover': float(r[16]), 'P_comm': float(r[17]), 'h_max': float(r[18]),
    }
    fleet = [(str(df.iloc[i][0]), str(df.iloc[i][1]), str(df.iloc[i][2])) for i in range(6, 8)]
    b = df.iloc[11]
    bat = {'R': dict(count=int(b[1]), T_full=float(b[2]))}
    return t, fleet, bat


def load_comm():
    f = os.path.join(DATA, '无人机应急物资运输基础数据', '通信链路参数.xlsx')
    df = pd.read_excel(f, sheet_name='数据', header=None)
    d = {}
    for i in range(2, 16):
        r = df.iloc[i]
        d[(str(r[0]), str(r[1]))] = (str(r[2]), float(r[3]))
    return d


# ----------------------------------------------------------------------------
# DEM [DEM GeoTIFF, 30 m, WGS84]
# ----------------------------------------------------------------------------
def load_dem():
    from dem_io import load_dem as _l
    return _l()


# ----------------------------------------------------------------------------
# 航段几何：水平距离 / 巡航海拔 / 爬升下降高度 [附录2]
# ----------------------------------------------------------------------------
def horizontal_m(lon1, lat1, lon2, lat2):
    """两节点水平直线距离（等距圆柱局部投影，山区 10 km 量级误差 <0.1%）。"""
    latm = math.radians((lat1 + lat2) / 2.0)
    dx = (lon2 - lon1) * DEG2M * math.cos(latm)
    dy = (lat2 - lat1) * DEG2M
    return math.hypot(dx, dy)


def leg_geometry(z, lon, lat, lon1, lat1, lon2, lat2):
    """返回航段几何 dict。

    巡航海拔统一取该航段所经 DEM 像元最高地面高程 + 50 m [附录2]；
    沿途按像元步长采样，覆盖整条水平直线。
    """
    d = horizontal_m(lon1, lat1, lon2, lat2)
    n = max(2, int(math.ceil(d / 30.0)) + 1)
    ts = np.linspace(0.0, 1.0, n)
    qlon = lon1 + (lon2 - lon1) * ts
    qlat = lat1 + (lat2 - lat1) * ts
    from dem_io import bilinear
    idx = np.clip(((qlon - lon[0]) / (lon[1] - lon[0])).round().astype(int), 0, lon.size - 1)
    idy = np.clip(((lat[0] - qlat) / (lat[0] - lat[1])).round().astype(int), 0, lat.size - 1)
    zs = z[idy, idx]
    zmax = float(np.nanmax(zs))
    zc = zmax + CLEARANCE
    return dict(d=d, z_cruise=zc, z_path=zs, z_max=zmax,
                qlon=qlon, qlat=qlat)


def leg_time_energy(ty, geom, q, start_h, end_h):
    """单航段时间与能耗 [附录2]。

    q        : 该航段有效载荷 kg（全程携带）
    start_h  : 起点作业高度（海拔 m）
    end_h    : 终点作业高度（海拔 m）
    """
    d = geom['d']
    zc = geom['z_cruise']
    h_up = max(0.0, zc - start_h)
    h_dn = max(0.0, zc - end_h)
    t = h_up / ty['v_up'] + d / ty['v_cruise'] + h_dn / ty['v_down']
    m = ty['m_empty'] + q
    # 水平巡航能耗 [附录2 Eq.3]：E_hor = E_use * d / L_g(q)（主口径，见 D1-a）
    P_hor = equivalent_cruise_power(ty, q)                       # kW（导出量）
    E_hor = equivalent_cruise_energy(ty, q, d)                   # kWh（主口径）
    E_up = (m * G * h_up / ty['eta_up']) / 3.6e6 if ty['eta_up'] > 0 else 0.0
    E_dn = (m * G * h_dn / ty['eta_down']) / 3.6e6 if ty['eta_down'] > 0 else 0.0
    return dict(t=t, E=E_hor + E_up + E_dn, E_hor=E_hor, E_up=E_up, E_dn=E_dn,
                h_up=h_up, h_dn=h_dn, d=d, P_hor=P_hor)



def equivalent_range(ty, q):
    """L_g(q) = L0 - (L0 - LF) (q/Q)^(3/2)   [附录2 Eq.1]"""
    r = min(1.0, max(0.0, q / ty['q_max']))
    return ty['L_empty'] - (ty['L_empty'] - ty['L_full']) * r ** 1.5


def equivalent_cruise_energy(ty, q, d):
    """在载荷 q 下水平巡航距离 d 的能耗（kWh）—— 【主口径】。

    口径（D1-a）：标准航程 L_g(q) 定义为"该载荷下耗尽单组可用能量 E_g^use
    的全部水平巡航航程"，故水平巡航能耗与距离成正比：
        E_hor(q, d) = E_g^use * d / L_g(q)          [kWh]
    量纲：kWh * m / m = kWh。与 [附录2 Eq.1] 的 L_g(q) 唯一相容。
    """
    L = equivalent_range(ty, q)
    return ty['E_use'] * d / L


def equivalent_cruise_power(ty, q):
    """水平巡航功率（kW）—— 【导出量，仅供报告/灵敏度用】。

        P_hor(q) = E_hor(q,d) / (d / v_cruise)
                 = E_g^use * v_cruise / L_g(q) * 3600   [kW]
    注意 3600 因子：E_g^use 以 kWh 计而 v/L 以 1/s 计，
    直接相乘得 kWh/s，须乘 3600 方为 kW。
    """
    L = equivalent_range(ty, q)
    return ty['E_use'] * ty['v_cruise'] / L * 3600.0



# ----------------------------------------------------------------------------
# 返航安全余量 [附录2 Eq.4]
# ----------------------------------------------------------------------------
def energy_ok(ty, E_sortie):
    return E_sortie <= (1.0 - ty['rho']) * ty['E_use']


def soc_end(ty, E_sortie):
    return 1.0 - E_sortie / ty['E_use']


# ----------------------------------------------------------------------------
# 中继无人机时间与能耗 [附录2 中继段] —— 与运输机型分开（参数结构不同）
# ----------------------------------------------------------------------------
def relay_transit(rt, geom, start_h, end_h):
    """中继单程转移：爬升 + 水平巡航 + 下降。

    水平巡航能耗 = 巡航功率 × 巡航时间 [附录2 中继段明文]
    爬升附加能耗 = m g h / eta_up（m 取计划起飞总质量）
    下降能耗效率 0 -> 不单独计算下降附加能耗
    """
    d, zc = geom['d'], geom['z_cruise']
    h_up = max(0.0, zc - start_h)
    h_dn = max(0.0, zc - end_h)
    t = h_up / rt['v_up'] + d / rt['v_cruise'] + h_dn / rt['v_down']
    E_cr = rt['P_cruise'] * (d / rt['v_cruise']) / 3600.0
    E_up = (rt['m_takeoff'] * G * h_up / rt['eta_up']) / 3.6e6 if rt['eta_up'] > 0 else 0.0
    E_dn = (rt['m_takeoff'] * G * h_dn / rt['eta_down']) / 3.6e6 if rt['eta_down'] > 0 else 0.0
    return dict(t=t, E=E_cr + E_up + E_dn, E_cr=E_cr, E_up=E_up, E_dn=E_dn,
                h_up=h_up, h_dn=h_dn, d=d)


def relay_hover_energy(rt, t_service):
    """通信服务能耗 = (悬停功率 + 通信附加功率) × 服务时长 [附录2 中继段]"""
    return (rt['P_hover'] + rt['P_comm']) * t_service / 3600.0


def relay_soc_end(rt, E_sortie):
    return 1.0 - E_sortie / rt['E_use']


def relay_energy_ok(rt, E_sortie):
    return E_sortie <= (1.0 - rt['rho']) * rt['E_use']


# ----------------------------------------------------------------------------
# 充电周转 [附录2 Eq.5]
# ----------------------------------------------------------------------------
def t_charge(s, T_full):
    s = float(np.clip(s, 0.0, 1.0))
    if s < 0.90:
        return T_full * (0.65 * (0.90 - s) / 0.90 + 0.35)
    return T_full * 0.35 * (1 - s) / 0.10


# ----------------------------------------------------------------------------
# 通信链路 [附录3]
# ----------------------------------------------------------------------------
def los_blocked(z, lon, lat, p1, p2, samples=400):
    """视线是否被地形遮挡。p = (lon, lat, h_abs)，h_abs 为海拔 m。

    判定：对连线做线性插值高度，与 DEM 地面高程比较；
    由于 DEM 为 DSM，端点自身像元不计入，留 1 个像元余量。
    """
    d = horizontal_m(p1[0], p1[1], p2[0], p2[1])
    if d < 1e-6:
        return False
    ts = np.linspace(0.0, 1.0, samples)
    qlon = p1[0] + (p2[0] - p1[0]) * ts
    qlat = p1[1] + (p2[1] - p1[1]) * ts
    hline = p1[2] + (p2[2] - p1[2]) * ts
    idx = np.clip(((qlon - lon[0]) / (lon[1] - lon[0])).round().astype(int), 0, lon.size - 1)
    idy = np.clip(((lat[0] - qlat) / (lat[0] - lat[1])).round().astype(int), 0, lat.size - 1)
    zg = z[idy, idx]
    return bool(np.any(hline < zg))


def link_available(z, lon, lat, ep_a, ep_b, params=None):
    """双向链路可用性 [附录3]。

    ep = (lon, lat, h_abs, role)；role 取值 'G01'|'T'|'RA'|'RB'。
    'T'  运输无人机：Pt 20 dBm, G 3 dBi
    'RA' 中继接入端：Pt 20 dBm, G 6 dBi
    'RB' 中继回传端：Pt 19 dBm, G 8 dBi
    'G01' 网关：Pt 27 dBm, G 12 dBi
    """
    role_tx = {'T': (20.0, 3.0), 'RA': (20.0, 6.0), 'RB': (19.0, 8.0), 'G01': (27.0, 12.0)}
    role_rx = {'T': 3.0, 'RA': 6.0, 'RB': 8.0, 'G01': 12.0}
    def dir_budget(tx, rx):
        Pt, Gt = role_tx[tx]
        Gr = role_rx[rx]
        return Pt + Gt + Gr - L_SYS - (P_SENS + M_FADE)
    Lmax = min(dir_budget(ep_a[3], ep_b[3]), dir_budget(ep_b[3], ep_a[3]))
    dist_km = horizontal_m(ep_a[0], ep_a[1], ep_b[0], ep_b[1]) / 1000.0
    dist3 = math.sqrt(dist_km ** 2 + ((ep_a[2] - ep_b[2]) / 1000.0) ** 2)
    fspl = 32.45 + 20 * math.log10(F_MHZ) + 20 * math.log10(max(dist3, 1e-9))
    blocked = los_blocked(z, lon, lat, ep_a, ep_b)
    lpath = fspl + (L_OBS if blocked else 0.0)
    return dict(avail=(lpath <= Lmax), Lmax=Lmax, Lpath=lpath, fspl=fspl,
                blocked=blocked, dist3_km=dist3)


def gateway_endpoint(nodes):
    o = nodes['O01']
    return (o['lon'], o['lat'], o['elev'] + G01_H, 'G01')


if __name__ == '__main__':
    nodes = load_nodes(); ty = load_transport_types()
    print('nodes', len(nodes), 'types', list(ty))
    for g, t in ty.items():
        print(g, 'L(q=0)=%.0f L(q=Q)=%.0f  P_hor(0)=%.3f kW  P_hor(Q)=%.3f kW  E_full_cruise=%.3f kWh'
              % (equivalent_range(t, 0), equivalent_range(t, t['q_max']),
                 equivalent_cruise_power(t, 0), equivalent_cruise_power(t, t['q_max']),
                 equivalent_cruise_power(t, t['q_max']) * equivalent_range(t, t['q_max'])
                 / t['v_cruise'] / 3600))
