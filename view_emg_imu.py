"""
Georgia Tech 数据集 EMG + IMU 可视化工具

使用 PySide6 + pyqtgraph，支持：
  - 选择受试者 (AB06 … AB28) 与运动条件 (levelground/ramp/stair/treadmill)
  - 上图: EMG 多通道叠加，左侧树勾选显隐
  - 中图: IMU 单通道，下拉选择
  - 下图: 右脚步态周期 (gcRight) 的原始锯齿斜坡
  - EMG / IMU 两个面板叠加步态事件竖线与支撑相色带
  - X 轴联动，鼠标滚轮缩放、拖拽平移

命令行:
    uv run python view_emg_imu.py
    uv run python view_emg_imu.py --subject AB12 --condition ramp
    uv run python view_emg_imu.py --data-dir D:\\path\\to\\data_GeorgiaTech_csv
"""

import sys
import argparse
import colorsys

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLabel, QSplitter, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QPushButton, QCheckBox, QGroupBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from gt_dataset import (
    CONDITION_LABELS, Dataset, add_data_args, resolve_dataset,
)
from gt_gait import (
    GC_SENSOR, HS_COLOR, TO_COLOR, STANCE_COLOR,
    GaitEvents, GaitOverlay, extract_events, load_walk_mask,
)


# 11 个 EMG 通道的缩写+中文名
EMG_LABELS = {
    "gastrocmed": "GM 腓肠肌内侧头",
    "tibialisanterior": "TA 胫骨前肌",
    "soleus": "SOL 比目鱼肌",
    "vastusmedialis": "VM 股内侧肌",
    "vastuslateralis": "VL 股外侧肌",
    "rectusfemoris": "RF 股直肌",
    "bicepsfemoris": "BF 股二头肌",
    "semitendinosus": "ST 半腱肌",
    "gracilis": "GR 股薄肌",
    "gluteusmedius": "GMed 臀中肌",
    "rightexternaloblique": "REO 右腹外斜肌",
}

# IMU 部位中文名
IMU_SEGMENT_LABELS = {
    "foot": "足部",
    "shank": "小腿",
    "thigh": "大腿",
    "trunk": "躯干",
}

# ── 渲染性能 ────────────────────────────────────────
# 抗锯齿: pyqtgraph 开启后会走 QPainterPath 描边, 而不是 drawLines 快路径。
# 一条 1.5 万点的曲线单次描边约 176ms, 11 条 EMG 曲线重绘一次要 2 秒以上,
# 切 trial 时界面基本冻住。关闭后走 drawLines, 同样效果快 30 倍。
ANTIALIAS = False

# 降采样密度: pyqtgraph 默认每像素画 5 个采样点 (autoDownsampleFactor=5),
# 视口约 1060px 时每条曲线要画 1.1 万个点, 拖拽时非常卡。
# 改成 1 (每像素 1 个采样点) 后拖拽流畅度提升约 6 倍;
# 放大时 pyqtgraph 会自动把采样间隔降回 1, 恢复全分辨率, 不影响细节。
DOWNSAMPLE_FACTOR = 1.0


def setup_curve(curve):
    """配置降采样 + 裁剪到视口, 保证缩放/平移流畅"""
    curve.setDownsampling(auto=True, method="peak")
    curve.setClipToView(True)
    # autoDownsampleFactor 在 pyqtgraph 0.14 没有公开 setter, 只能直接改 opts
    if "autoDownsampleFactor" in curve.opts:
        curve.opts["autoDownsampleFactor"] = DOWNSAMPLE_FACTOR
    return curve


def color_swatch(color: str, text: str) -> QLabel:
    """侧栏图例的小色块 (HS 跟触 / TO 离地 / 支撑相)。"""
    label = QLabel(f" {text} ")
    label.setStyleSheet(
        f"background-color: {color}; color: #ffffff;"
        f" border-radius: 3px; padding: 1px 5px;")
    label.setToolTip(f"{text}  ({color})")
    return label


def imu_label(ch: str) -> str:
    """生成 IMU 通道的友好标签，如 'foot_Accel_X' → '足部 加速度 X'。"""
    parts = ch.split("_")
    if len(parts) >= 3:
        seg = parts[0]
        sensor = parts[1]
        axis = parts[2]
        seg_cn = IMU_SEGMENT_LABELS.get(seg, seg)
        sensor_cn = "加速度" if sensor == "Accel" else "陀螺仪"
        return f"{seg_cn} {sensor_cn} {axis} ({ch})"
    return ch


class EMGIMUViewer(QMainWindow):
    """EMG + IMU 可视化主窗口"""

    #: 本视图依赖的传感器；trial 列表只列出 emg 与 imu 都存在的文件
    REQUIRED_SENSORS = ("emg", "imu")

    def __init__(self, dataset: Dataset, subject: str | None = None,
                 condition: str | None = None, trial: str | None = None):
        super().__init__()
        self.dataset = dataset
        self.resize(1400, 1000)

        self._emg_curves: list[pg.PlotDataItem] = []
        self._imu_df: pd.DataFrame | None = None
        self._imu_curve: pg.PlotDataItem | None = None

        # 步态周期 (gcRight) 相关状态
        self._t0: float = 0.0              # 三个面板统一的时间零点 (EMG 起始)
        self._gc_df: pd.DataFrame | None = None       # gcRight 原始表
        self._gc_events: GaitEvents | None = None     # 提取出的事件
        self._gc_walk_mask: np.ndarray | None = None   # 「算不算在走」的行级掩码
        # 一个叠加层实例只服务一个面板 —— pyqtgraph 的图元只能存在于一个场景里
        self._gc_overlay_emg = GaitOverlay()
        self._gc_overlay_imu = GaitOverlay()

        self._build_ui()
        self._populate_subjects(subject)
        self._reload_conditions(condition)
        self._load_trials(trial)

    # ── UI 构建 ──────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 顶部控制栏
        bar = QHBoxLayout()
        bar.addWidget(QLabel("受试者:"))
        self.cmb_subject = QComboBox()
        self.cmb_subject.currentIndexChanged.connect(self._on_subject_changed)
        bar.addWidget(self.cmb_subject)

        bar.addWidget(QLabel("条件:"))
        self.cmb_cond = QComboBox()
        self.cmb_cond.currentIndexChanged.connect(self._on_cond_changed)
        bar.addWidget(self.cmb_cond)

        bar.addWidget(QLabel("Trial:"))
        self.cmb_trial = QComboBox()
        self.cmb_trial.setMinimumWidth(350)
        self.cmb_trial.currentIndexChanged.connect(self._on_trial_changed)
        bar.addWidget(self.cmb_trial)

        bar.addStretch()
        root.addLayout(bar)

        # 左侧栏 + 右双图
        splitter = QSplitter(Qt.Horizontal)

        # 左: EMG 通道勾选树 + IMU 通道下拉
        sidebar = QWidget()
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)

        sb_layout.addWidget(QLabel("EMG 通道:"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.itemChanged.connect(self._on_item_changed)
        sb_layout.addWidget(self.tree)

        btn_row = QHBoxLayout()
        self.btn_all_on = QPushButton("全显")
        self.btn_all_on.clicked.connect(lambda: self._set_all(True))
        btn_row.addWidget(self.btn_all_on)
        self.btn_all_off = QPushButton("全隐")
        self.btn_all_off.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(self.btn_all_off)
        sb_layout.addLayout(btn_row)

        sb_layout.addSpacing(10)
        sb_layout.addWidget(QLabel("IMU 通道:"))
        self.cmb_imu = QComboBox()
        self.cmb_imu.currentIndexChanged.connect(self._on_imu_changed)
        sb_layout.addWidget(self.cmb_imu)

        sb_layout.addSpacing(10)
        sb_layout.addWidget(self._build_gc_box())

        sb_layout.addStretch()
        splitter.addWidget(sidebar)

        # 右: 三图 (EMG / IMU / 步态周期)
        pg.setConfigOptions(antialias=ANTIALIAS)
        plot_splitter = QSplitter(Qt.Vertical)

        # 上图: EMG (白色背景, 无图例)
        self.plot_emg = pg.PlotWidget()
        self.plot_emg.getAxis("left").enableAutoSIPrefix(False)
        self.plot_emg.setBackground("w")
        self.plot_emg.setLabel("left", "EMG (V)")
        self.plot_emg.showGrid(x=True, y=True, alpha=0.3)
        plot_splitter.addWidget(self.plot_emg)

        # 中图: IMU (白色背景)
        self.plot_imu = pg.PlotWidget()
        self.plot_imu.setBackground("w")
        self.plot_imu.getAxis("left").enableAutoSIPrefix(False)
        self.plot_imu.setLabel("left", "IMU")
        self.plot_imu.setLabel("bottom", "时间", units="s")
        self.plot_imu.showGrid(x=True, y=True, alpha=0.3)
        plot_splitter.addWidget(self.plot_imu)

        # 下图: 右脚步态周期 (gcRight 的原始锯齿斜坡)
        self.plot_gc = pg.PlotWidget()
        self.plot_gc.setBackground("w")
        self.plot_gc.getAxis("left").enableAutoSIPrefix(False)
        self.plot_gc.setLabel("left", "周期相位 (%)")
        self.plot_gc.showGrid(x=True, y=True, alpha=0.3)
        # 图例排成一行 —— GC 面板很矮, 竖排两行会盖住斜坡
        self.plot_gc.addLegend(offset=(10, 8), labelTextSize="9pt")
        self.plot_gc.plotItem.legend.setColumnCount(2)
        plot_splitter.addWidget(self.plot_gc)

        # X 轴联动 (EMG 面板是主控方)
        self.plot_imu.setXLink(self.plot_emg)
        self.plot_gc.setXLink(self.plot_emg)

        plot_splitter.setSizes([360, 300, 190])
        splitter.addWidget(plot_splitter)

        splitter.setSizes([250, 1150])
        root.addWidget(splitter)

    # ── 步态周期 (GC) 控件 ──────────────────────────
    def _build_gc_box(self) -> QGroupBox:
        """侧栏的步态周期控制组。"""
        box = QGroupBox(f"步态周期 ({GC_SENSOR})")
        self.gc_box = box
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 8)

        # 图例: 三个色块
        legend = QHBoxLayout()
        legend.addWidget(color_swatch(HS_COLOR, "HS 跟触"))
        legend.addWidget(color_swatch(TO_COLOR, "TO 离地"))
        legend.addWidget(color_swatch(STANCE_COLOR, "支撑相"))
        legend.addStretch()
        lay.addLayout(legend)

        self.chk_gc_lines = QCheckBox("事件竖线 (HS / TO)")
        self.chk_gc_lines.setChecked(True)
        self.chk_gc_lines.toggled.connect(
            lambda _checked: self._apply_gc_visibility())
        lay.addWidget(self.chk_gc_lines)

        self.chk_gc_ribbon = QCheckBox("支撑相色带")
        self.chk_gc_ribbon.setChecked(True)
        self.chk_gc_ribbon.toggled.connect(
            lambda _checked: self._apply_gc_visibility())
        lay.addWidget(self.chk_gc_ribbon)

        self.chk_gc_walk_only = QCheckBox("仅显示步行段")
        self.chk_gc_walk_only.setToolTip(
            "按 conditions/Label 过滤掉 idle / stand 段里的伪事件\n"
            "(跑台的 conditions 只有 Speed 列, 没有 Label, 不可用)")
        self.chk_gc_walk_only.toggled.connect(
            lambda _checked: self._rebuild_gait())
        lay.addWidget(self.chk_gc_walk_only)

        return box

    def _apply_gc_visibility(self):
        """显隐开关 → 直接改图元可见性 (不重建)。"""
        for overlay in (self._gc_overlay_emg, self._gc_overlay_imu):
            overlay.set_visible(
                ribbon=self.chk_gc_ribbon.isChecked(),
                hs=self.chk_gc_lines.isChecked(),
                to=self.chk_gc_lines.isChecked(),
            )

    def _rebuild_gait(self):
        """按当前开关重新提取步态事件并刷新叠加层。

        「仅显示步行段」会改变事件集合, 所以要重建; 显隐开关只改可见性。
        """
        events = None
        if self._gc_df is not None:
            keep = self._gc_walk_mask if self.chk_gc_walk_only.isChecked() else None
            events = extract_events(self._gc_df, self._t0, keep=keep)
        self._gc_events = events

        for overlay in (self._gc_overlay_emg, self._gc_overlay_imu):
            overlay.build(events)
        self._gc_overlay_emg.attach(self.plot_emg)
        self._gc_overlay_imu.attach(self.plot_imu)
        self._apply_gc_visibility()
        self._update_gc_controls()

    def _update_gc_controls(self):
        """按可用性置灰控件。"""
        has_gc = self._gc_df is not None
        has_events = self._gc_events is not None and not self._gc_events.is_empty
        self.chk_gc_lines.setEnabled(has_events)
        self.chk_gc_ribbon.setEnabled(has_events)
        # 「仅显示步行段」需要 conditions/Label —— 跑台的 conditions 只有 Speed
        self.chk_gc_walk_only.setEnabled(has_gc and self._gc_walk_mask is not None)
        self.gc_box.setEnabled(has_gc)
        if has_gc:
            self.gc_box.setTitle(f"步态周期 ({GC_SENSOR})")
        else:
            self.gc_box.setTitle(f"步态周期 ({GC_SENSOR}) — 本 trial 无此文件")

    def _gc_info_text(self) -> str:
        """状态栏里的 gcRight 摘要。"""
        if self._gc_df is None:
            return "  |  gcRight: 无"
        events = self._gc_events
        if events is None or events.is_empty:
            return "  |  gcRight: 无事件"
        return (f"  |  gcRight: {len(events)} 步 "
                f"(HS {events.t_hs.size} / TO {events.t_to.size})")

    # ── 数据加载 ──────────────────────────────────────
    def _populate_subjects(self, preselect: str | None = None):
        """填充受试者下拉框（数据来自数据集扫描，转换中新出现的受试者也能看到）"""
        self.cmb_subject.blockSignals(True)
        self.cmb_subject.clear()
        for subject in self.dataset.subjects:
            self.cmb_subject.addItem(subject, subject)

        if preselect:
            idx = self._find_subject(preselect)
            if idx >= 0:
                self.cmb_subject.setCurrentIndex(idx)
        self.cmb_subject.blockSignals(False)
        self._update_title()

    def _find_subject(self, name: str) -> int:
        """按名字找受试者下标，大小写不敏感；找不到返回 -1"""
        for i in range(self.cmb_subject.count()):
            if self.cmb_subject.itemData(i).upper() == name.upper():
                return i
        return -1

    def _reload_conditions(self, preselect: str | None = None):
        """按当前受试者刷新运动条件下拉框"""
        subject = self.cmb_subject.currentData()
        prev = self.cmb_cond.currentData()

        conds = self.dataset.conditions_for(subject, *self.REQUIRED_SENSORS)

        self.cmb_cond.blockSignals(True)
        self.cmb_cond.clear()
        for c in conds:
            self.cmb_cond.addItem(f"{CONDITION_LABELS[c]} ({c})", c)

        # 优先用命令行指定的，否则尽量保持用户当前选中的条件
        target = preselect if preselect in conds else prev
        if target in conds:
            self.cmb_cond.setCurrentIndex(conds.index(target))
        self.cmb_cond.blockSignals(False)

    def _load_trials(self, preselect: str | None = None):
        """加载当前受试者 + 条件下的 trial 列表 (只列出 EMG 和 IMU 同时存在的)"""
        subject = self.cmb_subject.currentData()
        cond = self.cmb_cond.currentData()

        self.cmb_trial.blockSignals(True)
        self.cmb_trial.clear()

        for fname in self.dataset.trials(subject, cond, *self.REQUIRED_SENSORS):
            self.cmb_trial.addItem(fname.replace(".csv", ""), fname)

        if preselect:
            target = preselect if preselect.endswith(".csv") else preselect + ".csv"
            idx = self.cmb_trial.findData(target)
            if idx >= 0:
                self.cmb_trial.setCurrentIndex(idx)

        self.cmb_trial.blockSignals(False)
        if self.cmb_trial.count() > 0:
            self._on_trial_changed()

    def _update_title(self):
        subject = self.cmb_subject.currentData() or "—"
        self.setWindowTitle(f"{subject} EMG / IMU 可视化")

    # ── 交互回调 ──────────────────────────────────────
    def _on_subject_changed(self):
        self._update_title()
        self._reload_conditions()
        self._load_trials()

    def _on_cond_changed(self):
        self._load_trials()

    def _on_trial_changed(self):
        subject = self.cmb_subject.currentData()
        cond = self.cmb_cond.currentData()
        fname = self.cmb_trial.currentData()
        if not subject or not cond or not fname:
            return

        emg_path = self.dataset.trial_path(subject, cond, "emg", fname)
        imu_path = self.dataset.trial_path(subject, cond, "imu", fname)
        if not emg_path.exists():
            return

        self.statusBar().showMessage(f"加载: {emg_path.name} ...")
        QApplication.processEvents()

        emg_df = pd.read_csv(emg_path)
        imu_df = pd.read_csv(imu_path) if imu_path.exists() else None

        # 步态周期是「可选加载」: 没有 gcRight 就降级, 绝不影响 trial 列表
        # (旧布局的 treadmill/gcRight 少一个文件, 所以不能进 REQUIRED_SENSORS)
        self._gc_df = None
        self._gc_walk_mask = None
        gc_path = self.dataset.trial_path(subject, cond, GC_SENSOR, fname)
        if gc_path.exists():
            try:
                self._gc_df = pd.read_csv(gc_path)
            except Exception:
                self._gc_df = None          # 转换中写了一半 —— 当作没有

        # conditions/Label 用于「仅显示步行段」过滤 (跑台没有 Label, 会是 None)
        cond_path = self.dataset.trial_path(subject, cond, "conditions", fname)
        if cond_path.exists() and self._gc_df is not None:
            try:
                self._gc_walk_mask = load_walk_mask(
                    pd.read_csv(cond_path),
                    self._gc_df["Header"].to_numpy(dtype=float))
            except Exception:
                self._gc_walk_mask = None

        self._plot(emg_df, imu_df)

        imu_info = f"  |  IMU {len(imu_df)} 行" if imu_df is not None else "  |  IMU: 无"
        self.statusBar().showMessage(
            f"{emg_path.name}  |  EMG {len(emg_df)} 行  |  "
            f"{len(emg_df.columns) - 1} 通道{imu_info}{self._gc_info_text()}"
        )

    # ── 绘图 ──────────────────────────────────────────
    def _plot(self, emg_df: pd.DataFrame, imu_df: pd.DataFrame | None):
        """绘制 EMG 各通道 + IMU 通道下拉 + 步态周期叠加层"""
        # 清除旧曲线
        self.plot_emg.clear()
        self._emg_curves.clear()

        # 步态叠加层要先摘, 再清面板 —— 顺序反了 attach() 的幂等判断会失准
        self._gc_overlay_emg.detach()
        self._gc_overlay_imu.detach()

        self.plot_imu.clear()
        self._imu_curve = None

        # 清除旧树
        self.tree.blockSignals(True)
        self.tree.clear()

        # 公共时间零点 (EMG 起始) —— EMG 面板是 X 轴主控方, 三个面板统一用它。
        # (跑台 trial 的 imu.t0 与 emg.t0 最多差 4 ms, 各自归零会让两图错位)
        self._t0 = t0 = emg_df["Header"].values[0]

        # ── EMG ──
        t_emg = emg_df["Header"].values - t0
        emg_channels = [c for c in emg_df.columns if c != "Header"]
        n = len(emg_channels)

        top = QTreeWidgetItem(self.tree, [f"EMG 通道 ({n})"])
        top.setCheckState(0, Qt.Checked)

        for i, ch in enumerate(emg_channels):
            y = emg_df[ch].values
            hue = i / n
            r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.9)
            color = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
            pen = pg.mkPen(color, width=1.2)

            curve = setup_curve(self.plot_emg.plot(t_emg, y, pen=pen))
            self._emg_curves.append(curve)

            label = f"{ch} ({EMG_LABELS.get(ch, ch)})"
            leaf = QTreeWidgetItem(top, [label])
            leaf.setCheckState(0, Qt.Checked)
            leaf.setData(0, Qt.UserRole, i)
            leaf.setForeground(0, QColor(color))

        self.tree.expandAll()
        self.tree.blockSignals(False)

        # ── IMU 下拉 ──
        self._imu_df = imu_df
        self.cmb_imu.blockSignals(True)
        self.cmb_imu.clear()
        if imu_df is not None:
            for ch in imu_df.columns:
                if ch == "Header":
                    continue
                self.cmb_imu.addItem(imu_label(ch), ch)
            self.cmb_imu.setCurrentIndex(0)
        self.cmb_imu.blockSignals(False)

        # ── 步态周期 ──
        # 先重建叠加层 (事件集), 再画 IMU 曲线 —— 顺序反了会把上一条 trial 的
        # 旧图元先挂上去再换掉
        self._rebuild_gait()
        self._plot_imu()
        self._plot_gc()

        self.plot_emg.autoRange()

    def _plot_imu(self):
        pw = self.plot_imu
        # 只摘掉上一条 IMU 曲线; 步态叠加层留着 —— 切通道时不必重建几百个图元
        if self._imu_curve is not None:
            pw.getPlotItem().removeItem(self._imu_curve)
            self._imu_curve = None
        # 叠加层挂回面板 (幂等)
        self._gc_overlay_imu.attach(pw)

        if self._imu_df is None:
            pw.setTitle("IMU — (无数据)", size="10pt")
            return
        i = self.cmb_imu.currentIndex()
        if i < 0:
            return
        ch = self.cmb_imu.itemData(i)
        t = self._imu_df["Header"].values - self._t0
        y = self._imu_df[ch].values
        self._imu_curve = setup_curve(
            pw.plot(t, y, pen=pg.mkPen("#2ca02c", width=1)))
        pw.setTitle(f"IMU — {imu_label(ch)}", size="10pt")
        self._auto_range_y(pw, y)

    def _plot_gc(self):
        """画 GC 面板: 两条原始锯齿斜坡 (数据原貌)。

        锯齿的垂直跳变就是事件本身, 所以这个面板不需要再画竖线。
        """
        pw = self.plot_gc
        pw.clear()
        if self._gc_df is None:
            pw.setTitle("步态周期 GC (右脚) — (无数据)", size="10pt")
            return
        t = self._gc_df["Header"].to_numpy(dtype=float) - self._t0
        for col, color, style, name in (
            ("HeelStrike", HS_COLOR, Qt.SolidLine, "跟触 HeelStrike"),
            ("ToeOff", TO_COLOR, Qt.DashLine, "离地 ToeOff"),
        ):
            y = self._gc_df[col].to_numpy(dtype=float)
            setup_curve(pw.plot(t, y, pen=pg.mkPen(color, width=1, style=style),
                                name=name))
        pw.setTitle("步态周期 GC (右脚) — 锯齿跳变处即事件", size="10pt")
        # 不用 autoRange(): pyqtgraph 0.14 的 setXLink 是双向的, 在联动面板上调
        # autoRange() 会把 X 范围推给所有面板, 用户的缩放会被重置。
        pw.setYRange(-5, 105, padding=0)

    @staticmethod
    def _auto_range_y(pw: pg.PlotWidget, y: np.ndarray):
        """只对 Y 轴自动定范围 (不动 X)。

        pyqtgraph 0.14 没有 ``autoRange(axis="y")`` (``ViewBox.autoRange`` 的签名是
        ``(padding, items, item)``), 而 ``setYRange(min, max)`` 的 min/max 又是必填的,
        所以只能自己算范围。

        不能直接用 ``pw.autoRange()``: 三个面板 X 轴联动, 而 0.14 的 ``setXLink``
        是双向的 —— 任何一个面板重算 X 都会推给所有面板, 用户的缩放就没了。
        """
        ymin, ymax = float(np.nanmin(y)), float(np.nanmax(y))
        if not (np.isfinite(ymin) and np.isfinite(ymax)) or ymax <= ymin:
            return
        pad = 0.06 * (ymax - ymin)
        pw.setYRange(ymin - pad, ymax + pad, padding=0)

    # ── 交互 ──────────────────────────────────────────
    def _on_item_changed(self, item: QTreeWidgetItem, col: int):
        """树勾选联动: 顶层级联, 叶子切换曲线显隐"""
        idx = item.data(0, Qt.UserRole)
        if idx is not None:
            # 叶子节点 → 切换对应曲线
            self._emg_curves[idx].setVisible(item.checkState(0) == Qt.Checked)
        else:
            # 顶层/组节点 → 级联到所有子节点
            state = item.checkState(0)
            self.tree.blockSignals(True)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, state)
                cidx = child.data(0, Qt.UserRole)
                if cidx is not None:
                    self._emg_curves[cidx].setVisible(state == Qt.Checked)
            self.tree.blockSignals(False)

    def _on_imu_changed(self):
        self._plot_imu()

    def _set_all(self, visible: bool):
        """全显/全隐"""
        state = Qt.Checked if visible else Qt.Unchecked
        self.tree.blockSignals(True)
        top = self.tree.topLevelItem(0)
        if top:
            top.setCheckState(0, state)
            for i in range(top.childCount()):
                top.child(i).setCheckState(0, state)
        self.tree.blockSignals(False)
        for curve in self._emg_curves:
            curve.setVisible(visible)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Georgia Tech 数据集 EMG / IMU 可视化")
    add_data_args(parser)
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    viewer = EMGIMUViewer(dataset, subject=args.subject,
                          condition=args.condition, trial=args.trial)
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
