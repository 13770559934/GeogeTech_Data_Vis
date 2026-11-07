"""
gt_gait.py — Georgia Tech 步态周期 (gcLeft/gcRight) 事件解析 + pyqtgraph 叠加层。

背景
----
`gcLeft/` / `gcRight/` 这两个传感器只有 3 列::

    Header, HeelStrike, ToeOff

`HeelStrike` / `ToeOff` **不是事件时间戳**，而是一条 0→100 的锯齿相位斜坡：
每个步态周期内线性爬升，事件发生的那一刻值瞬间从 100 跳回 0::

    v[s]        == 0.0      ← 事件发生的那一拍 (s = 斜坡「离开零点」的下标)
    v[s_next-1] == 100.0    ← 下一个事件前一拍

爬升是沿**行索引**线性的 (``linspace(0, 100, n)``，实测 25917/25917 个片段吻合)，
所以斜坡的陡峭程度反映的是「这个周期有多少个采样点」。

事件下标必须用::

    s = np.where((v[:-1] == 0.0) & (v[1:] > 0.0))[0]

**不能**用 ``np.diff(v) < 0``: 每个 trial 开头有一段字面 0（人还没起步），
那样会漏掉每列的第一个事件（实测跑台 HS 125 vs 124）。
尾部「归零后不再上升」的截断，规范规则反而会自动排除。

事件时刻 = ``Header[s]``。

左腿 / 右腿
-----------
`gcLeft` 与 `gcRight` 的对应关系（MATLAB 读原始 `.mat` 确认）：

======================  ==========================  ==============================
条件                    ``HeelStrike``             ``ToeOff``
======================  ==========================  ==============================
levelground/ramp/stair  左文件=左腿，右文件=右腿   同左，各是本腿
**treadmill**           左文件=左腿，右文件=右腿   **两份逐字节相同（都是右脚）**
======================  ==========================  ==============================

→ 跑台**拿不到左脚离地**；地面条件可以。

本模块只依赖 numpy / pandas (解析) 与 pyqtgraph (画图)，不 import 任何 Qt widget，
所以解析部分可以不开 QApplication 直接跑、直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt

# ── 常量 ─────────────────────────────────────────────────────────────────────

#: 步态传感器目录名。驼峰，大小写敏感 —— 其余传感器目录全小写。
GC_SENSOR = "gcRight"

#: 跟触 (HeelStrike) 事件竖线 —— 红色实线。
HS_COLOR = "#d62728"
HS_RGBA = (214, 39, 40, 120)          # 半透明版（画在曲线上时用）
#: 离地 (ToeOff) 事件竖线 —— 蓝色虚线。
TO_COLOR = "#1f77b4"
TO_RGBA = (31, 119, 180, 120)
#: 支撑相色带 —— 琥珀色。
STANCE_COLOR = "#f0a860"
STANCE_RGBA = (240, 168, 96, 55)

#: 支撑相色带占面板高度的比例。``LinearRegionItem`` 的 ``span`` 是**视口**比例
#: 而不是数据比例，所以 Y 轴自动缩放 / 换 IMU 通道时色带位置纹丝不动。
RIBBON_SPAN = (0.0, 0.07)

#: ``conditions/Label`` 里代表「没在走」的标签。只排这两个 ——
#: turn1/turn2/rampascent/stairdescent 也真在走，排掉会丢真实的步态周期。
NON_WALK_LABELS = frozenset({"idle", "stand"})


# ── 数据结构 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GaitEvents:
    """一次 trial 的步态事件（时间已换算成相对 ``t0`` 的秒）。

    ``t_hs`` / ``t_to`` 各自是升序的事件时刻数组，**两者不按下标对齐**
    （trial 常从摆动相中途开始，第一个 TO 会出现在第一个 HS 之前）。
    """

    t_hs: np.ndarray      # 跟触时刻
    t_to: np.ndarray      # 离地时刻
    t0: float             # 时间零点

    def __len__(self) -> int:
        return int(self.t_hs.size)

    @property
    def is_empty(self) -> bool:
        return self.t_hs.size == 0 and self.t_to.size == 0


# ── 解析 ─────────────────────────────────────────────────────────────────────

def find_event_indices(ramp: np.ndarray) -> np.ndarray:
    """从 0→100 锯齿斜坡里取出事件下标（斜坡「离开零点」的那一拍）。

    ``ramp[s] == 0.0`` 且 ``ramp[s+1] > 0.0``。文件开头那段字面 0 不会被误判，
    因为只有真正「上升」的那一拍才满足条件。
    """
    ramp = np.asarray(ramp, dtype=float)
    if ramp.size < 2:
        return np.empty(0, dtype=np.int64)
    return np.where((ramp[:-1] == 0.0) & (ramp[1:] > 0.0))[0]


def extract_events(gc_df: pd.DataFrame, t0: float,
                   keep: np.ndarray | None = None) -> GaitEvents:
    """从 gcRight 的 DataFrame 里提取 HS / TO 事件时刻。

    参数
    ----
    gc_df : 读进来的 gcRight CSV（``Header, HeelStrike, ToeOff``）
    t0    : 时间零点（秒），一般用 emg 的 ``Header[0]``
    keep  : 可选布尔掩码，长度与 ``gc_df`` 行数相同；``False`` 行上的事件被丢掉
            （用来做「仅显示步行段」过滤）
    """
    header = gc_df["Header"].to_numpy(dtype=float)
    hs_idx = find_event_indices(gc_df["HeelStrike"].to_numpy(dtype=float))
    to_idx = find_event_indices(gc_df["ToeOff"].to_numpy(dtype=float))
    if keep is not None:
        keep = np.asarray(keep, dtype=bool)
        if keep.size != header.size:
            raise ValueError(
                f"keep 掩码长度 {keep.size} 与 gc 行数 {header.size} 不一致")
        hs_idx = hs_idx[keep[hs_idx]]
        to_idx = to_idx[keep[to_idx]]
    return GaitEvents(t_hs=header[hs_idx] - t0,
                      t_to=header[to_idx] - t0,
                      t0=t0)


def pair_stances(t_hs: np.ndarray, t_to: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray]:
    """把每个跟触事件和它**之后最近**的离地事件配成一段支撑相。

    HS 列表和 TO 列表**不是下标对齐的** —— trial 通常从摆动相中途开始，
    第一个 TO 出现在第一个 HS 之前。按下标 zip 会配出负时长的支撑相
    （实测 −0.66 s），必须按时间顺序配。

    没有后继 TO 的最后一个 HS 不产生支撑相（trial 在支撑相中途结束）。
    """
    t_hs = np.asarray(t_hs, dtype=float)
    t_to = np.asarray(t_to, dtype=float)
    if t_hs.size == 0 or t_to.size == 0:
        return np.empty(0), np.empty(0)
    j = np.searchsorted(t_to, t_hs, side="right")
    valid = j < t_to.size
    return t_hs[valid], t_to[j[valid]]


def load_walk_mask(cond_df: pd.DataFrame | None,
                   gc_header: np.ndarray | None = None) -> np.ndarray | None:
    """按 ``conditions`` 的 ``Label`` 列算出「这一行算不算在走」的布尔数组。

    * 没有 ``Label`` 列（跑台只有 ``Speed``；旧镜像的坏 conditions 文件只有一列
      ``stand``）→ 返回 None，表示这个过滤不可用；
    * ``Label`` 落在 ``NON_WALK_LABELS``（idle / stand）→ False，其余 → True。

    地面条件下 ``conditions`` 与 ``gcRight`` 同为 200 Hz 且 ``Header`` 逐位相同，
    所以标签可以直接按行号对应。时间原点不一致时（如跑台的 conditions 是
    1000 Hz 的相对时间轴）也返回 None。

    返回数组长度与 ``gc_header`` 相同。
    """
    if cond_df is None or "Label" not in cond_df.columns:
        return None
    if gc_header is None:
        return None

    gc_header = np.asarray(gc_header, dtype=float)
    cond_header = cond_df["Header"].to_numpy(dtype=float)
    if gc_header.size == 0 or cond_header.size == 0:
        return None
    # 时间原点必须一致，否则标签对不上（跑台的 conditions 用相对时间轴）
    if abs(cond_header[0] - gc_header[0]) > 0.01:
        return None

    idx = np.clip(np.searchsorted(cond_header, gc_header), 0, cond_header.size - 1)
    labels = cond_df["Label"].astype(str).to_numpy()
    return ~np.isin(labels[idx], list(NON_WALK_LABELS))


# ── pyqtgraph 叠加层 ─────────────────────────────────────────────────────────

class GaitOverlay:
    """把步态事件画到一个 ``pg.PlotWidget`` 上。

    设计要点
    --------
    * **一个实例只服务一个面板** —— pyqtgraph 的图元只能存在于一个场景里，
      所以 EMG 面板和 IMU 面板各要一个实例（事件数据共享）。
    * **建一次、反复重挂** —— IMU 通道切换时面板只摘曲线，叠加层原地不动，
      不必重建几百个图元。
    * 所有图元都走 ``plot_item.addItem(item, ignoreBounds=True)`` ——
      否则自动量程会把 X 轴撑到步态事件的时间跨度上，经 X 联动把别的面板视野带歪。
    * 支撑相用 ``LinearRegionItem`` + ``span`` 画成贴底的色带：
      ``span`` 是**视口**比例，Y 轴自动缩放时位置不变。
    * **事件竖线只用两个图元** —— 一条 ``InfiniteLine`` 一个事件的话，
      跑台一个 trial 就有 250 条，三个联动面板每帧要过 1000 多个图元
      （实测 +42 ms/帧）。改成「一条 NaN 断开的折线画全部竖线」后
      每个面板只剩 2 个图元，帧开销降到 +6.6 ms。
      折线的 y 坐标跟着视口的 Y 范围走（靠 ``sigYRangeChanged`` 更新），
      所以竖线始终贯通整个面板高度。
    """

    def __init__(self) -> None:
        self._items: list[pg.GraphicsItem] = []
        self._regions: list[pg.LinearRegionItem] = []
        self._hs_line: pg.PlotDataItem | None = None
        self._to_line: pg.PlotDataItem | None = None
        self._hs_x: np.ndarray = np.empty(0)
        self._to_x: np.ndarray = np.empty(0)
        self._plot_item: pg.PlotItem | None = None

    # ── 建 / 挂 / 摘 ────────────────────────────────────────────────────────
    def build(self, events: GaitEvents | None) -> None:
        """按事件重建图元（只建对象，不入场景）。"""
        self.detach()
        self._regions.clear()
        self._hs_line = None
        self._to_line = None
        self._hs_x = np.empty(0)
        self._to_x = np.empty(0)
        self._items.clear()
        if events is None or events.is_empty:
            return

        # 支撑相色带：跟触 → 其后最近的离地
        t_hs, t_to = pair_stances(events.t_hs, events.t_to)
        for a, b in zip(t_hs, t_to):
            region = pg.LinearRegionItem(
                values=(float(a), float(b)),
                orientation="vertical",
                movable=False,
                span=RIBBON_SPAN,
                brush=pg.mkBrush(*STANCE_RGBA),
                pen=pg.mkPen(None),          # 不要边框，边界交给下面的事件竖线
            )
            # LinearRegionItem 内部自带 2 条边框 InfiniteLine。边框本来就是
            # NoPen（看不见），但它们仍会在每次视口变化时跑一遍 viewRangeChanged
            # —— 124 个色带就是 248 条多余的线。藏起来省掉这部分开销。
            for line in region.lines:
                line.setVisible(False)
            region.setZValue(-30)           # 压在曲线下面
            self._regions.append(region)

        # 事件竖线：每类一条 NaN 断开的折线
        self._hs_x = np.asarray(events.t_hs, dtype=float)
        self._to_x = np.asarray(events.t_to, dtype=float)
        self._hs_line = self._make_line_curve(self._hs_x, HS_RGBA, solid=True)
        self._to_line = self._make_line_curve(self._to_x, TO_RGBA, solid=False)

        self._items = [*self._regions]
        if self._hs_line is not None:
            self._items.append(self._hs_line)
        if self._to_line is not None:
            self._items.append(self._to_line)

    @staticmethod
    def _line_polyline(positions: np.ndarray, y_lo: float, y_hi: float
                       ) -> tuple[np.ndarray, np.ndarray]:
        """把一串事件时刻做成一条「NaN 断开的竖线折线」。

        每个事件占 3 个点: (x, y_lo) → (x, y_hi) → NaN。NaN 把折线断开，
        于是 pyqtgraph 会画成一根根独立的竖线，但它们共用**一个**图元。
        """
        n = positions.size
        x = np.empty(n * 3)
        x[0::3] = positions
        x[1::3] = positions
        x[2::3] = np.nan
        y = np.empty(n * 3)
        y[0::3] = y_lo
        y[1::3] = y_hi
        y[2::3] = np.nan
        return x, y

    def _make_line_curve(self, positions: np.ndarray, color, solid: bool
                         ) -> pg.PlotDataItem | None:
        """建一条画全部事件竖线的折线。"""
        if positions.size == 0:
            return None
        x, y = self._line_polyline(positions, 0.0, 1.0)
        style = Qt.SolidLine if solid else Qt.DashLine
        curve = pg.PlotDataItem(x, y, pen=pg.mkPen(color, width=1, style=style))
        curve.setZValue(-20)
        return curve

    def _update_line_extent(self):
        """把竖线折线的 y 坐标拉成当前视口的 Y 范围 —— 竖线贯通整个面板高度。"""
        if self._plot_item is None:
            return
        y_lo, y_hi = self._plot_item.getViewBox().viewRange()[1]
        for curve, positions in ((self._hs_line, self._hs_x),
                                 (self._to_line, self._to_x)):
            if curve is None or positions.size == 0:
                continue
            x, y = self._line_polyline(positions, y_lo, y_hi)
            curve.setData(x=x, y=y)

    def attach(self, pw: pg.PlotWidget) -> None:
        """把叠加层挂到面板上（幂等）。"""
        plot_item = pw.getPlotItem()
        if self._plot_item is plot_item:
            return
        self.detach()
        for item in self._items:
            plot_item.addItem(item, ignoreBounds=True)
        self._plot_item = plot_item
        # 视口 Y 范围变了要跟着更新竖线高度（X 变化不用管）
        plot_item.getViewBox().sigYRangeChanged.connect(self._update_line_extent)
        self._update_line_extent()

    def detach(self) -> None:
        """把叠加层从面板上摘下来（图元对象保留，之后还能挂回去）。"""
        if self._plot_item is None:
            return
        try:
            self._plot_item.getViewBox().sigYRangeChanged.disconnect(
                self._update_line_extent)
        except (RuntimeError, TypeError):
            pass
        for item in self._items:
            self._plot_item.removeItem(item)
        self._plot_item = None

    # ── 显隐 ────────────────────────────────────────────────────────────────
    def set_visible(self, ribbon: bool = True, hs: bool = True,
                    to: bool = True) -> None:
        """三个开关 → 图元显隐。"""
        for region in self._regions:
            region.setVisible(ribbon)
        if self._hs_line is not None:
            self._hs_line.setVisible(hs)
        if self._to_line is not None:
            self._to_line.setVisible(to)

    # ── 内部 ────────────────────────────────────────────────────────────────
    @property
    def n_items(self) -> int:
        """当前图元总数（色带 + 竖线折线），用于自检。"""
        return len(self._items)
