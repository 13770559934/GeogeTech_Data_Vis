"""
AB06 数据集 EMG + IMU 可视化工具

使用 PySide6 + pyqtgraph，支持：
  - 按 4 种运动条件 (levelground/ramp/stair/treadmill) 选择 trial
  - 上图: EMG 多通道叠加，左侧树勾选显隐
  - 下图: IMU 单通道，下拉选择
  - X 轴联动，鼠标滚轮缩放、拖拽平移
"""

import sys
import os
import colorsys
from pathlib import Path

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLabel, QSplitter, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

# ── 常量 ──────────────────────────────────────────────
DATA_DIR = Path(__file__).parent

CONDITIONS = ["levelground", "ramp", "stair", "treadmill"]

CONDITION_LABELS = {
    "levelground": "平地行走",
    "ramp": "坡道行走",
    "stair": "楼梯行走",
    "treadmill": "跑步机行走",
}

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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AB06 EMG / IMU 可视化")
        self.resize(1400, 900)

        self._emg_curves: list[pg.PlotDataItem] = []
        self._imu_df: pd.DataFrame | None = None

        self._build_ui()
        self._load_trials()

    # ── UI 构建 ──────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 顶部控制栏
        bar = QHBoxLayout()
        bar.addWidget(QLabel("条件:"))
        self.cmb_cond = QComboBox()
        for c in CONDITIONS:
            self.cmb_cond.addItem(f"{CONDITION_LABELS[c]} ({c})", c)
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

        sb_layout.addStretch()
        splitter.addWidget(sidebar)

        # 右: 上下双图
        pg.setConfigOptions(antialias=ANTIALIAS)
        plot_splitter = QSplitter(Qt.Vertical)

        # 上图: EMG (白色背景, 无图例)
        self.plot_emg = pg.PlotWidget()
        self.plot_emg.getAxis("left").enableAutoSIPrefix(False)
        self.plot_emg.setBackground("w")
        self.plot_emg.setLabel("left", "EMG (V)")
        self.plot_emg.showGrid(x=True, y=True, alpha=0.3)
        plot_splitter.addWidget(self.plot_emg)

        # 下图: IMU (白色背景)
        self.plot_imu = pg.PlotWidget()
        self.plot_imu.setBackground("w")
        self.plot_imu.getAxis("left").enableAutoSIPrefix(False)
        self.plot_imu.setLabel("left", "IMU")
        self.plot_imu.setLabel("bottom", "时间", units="s")
        self.plot_imu.showGrid(x=True, y=True, alpha=0.3)
        plot_splitter.addWidget(self.plot_imu)

        # X 轴联动
        self.plot_imu.setXLink(self.plot_emg)

        plot_splitter.setSizes([450, 450])
        splitter.addWidget(plot_splitter)

        splitter.setSizes([250, 1150])
        root.addWidget(splitter)

    # ── 数据加载 ──────────────────────────────────────
    def _load_trials(self):
        """加载当前条件下的 trial 列表 (只列出 EMG 和 IMU 同时存在的)"""
        self.cmb_trial.blockSignals(True)
        self.cmb_trial.clear()

        cond = self.cmb_cond.currentData()
        emg_dir = DATA_DIR / cond / "emg"
        imu_dir = DATA_DIR / cond / "imu"
        if emg_dir.is_dir():
            files = sorted(f for f in os.listdir(emg_dir) if f.endswith(".csv"))
            for f in files:
                if imu_dir.is_dir() and (imu_dir / f).exists():
                    self.cmb_trial.addItem(f.replace(".csv", ""), f)

        self.cmb_trial.blockSignals(False)
        if self.cmb_trial.count() > 0:
            self._on_trial_changed()

    def _on_cond_changed(self):
        self._load_trials()

    def _on_trial_changed(self):
        cond = self.cmb_cond.currentData()
        fname = self.cmb_trial.currentData()
        if not cond or not fname:
            return

        emg_path = DATA_DIR / cond / "emg" / fname
        imu_path = DATA_DIR / cond / "imu" / fname
        if not emg_path.exists():
            return

        self.statusBar().showMessage(f"加载: {emg_path.name} ...")
        QApplication.processEvents()

        emg_df = pd.read_csv(emg_path)
        imu_df = pd.read_csv(imu_path) if imu_path.exists() else None

        self._plot(emg_df, imu_df)

        imu_info = f"  |  IMU {len(imu_df)} 行" if imu_df is not None else "  |  IMU: 无"
        self.statusBar().showMessage(
            f"{emg_path.name}  |  EMG {len(emg_df)} 行  |  {len(emg_df.columns) - 1} 通道{imu_info}"
        )

    # ── 绘图 ──────────────────────────────────────────
    def _plot(self, emg_df: pd.DataFrame, imu_df: pd.DataFrame | None):
        """绘制 EMG 各通道 + IMU 通道下拉"""
        # 清除旧曲线
        self.plot_emg.clear()
        self._emg_curves.clear()

        self.plot_imu.clear()

        # 清除旧树
        self.tree.blockSignals(True)
        self.tree.clear()

        # 公共时间零点 (EMG 起始)
        t0 = emg_df["Header"].values[0]

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
        self._plot_imu()

        self.plot_emg.autoRange()

    def _plot_imu(self):
        pw = self.plot_imu
        pw.clear()
        if self._imu_df is None:
            pw.setTitle("IMU — (无数据)", size="10pt")
            return
        i = self.cmb_imu.currentIndex()
        if i < 0:
            return
        ch = self.cmb_imu.itemData(i)
        t0 = self._imu_df["Header"].values[0]
        t = self._imu_df["Header"].values - t0
        y = self._imu_df[ch].values
        curve = setup_curve(pw.plot(t, y, pen=pg.mkPen("#2ca02c", width=1)))
        pw.setTitle(f"IMU — {imu_label(ch)}", size="10pt")
        pw.autoRange()

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


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    viewer = EMGIMUViewer()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
