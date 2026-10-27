"""
AB06 数据集 EMG 可视化工具

使用 PySide6 + pyqtgraph，支持：
  - 按 4 种运动条件 (levelground/ramp/stair/treadmill) 选择 trial
  - 11 通道 EMG 信号叠加显示，左侧树勾选显隐
  - 鼠标滚轮缩放、拖拽平移 (pyqtgraph 内置)
"""

import sys
import os
import colorsys
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLabel, QSplitter, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QPushButton, QCheckBox,
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

# ── 渲染性能 ────────────────────────────────────────
# 抗锯齿: pyqtgraph 开启后会走 QPainterPath 描边, 而不是 drawLines 快路径。
# 一条 1.5 万点的曲线单次描边约 176ms, 11 条曲线重绘一次要 2 秒以上,
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


class EMGViewer(QMainWindow):
    """EMG 可视化主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AB06 EMG 可视化")
        self.resize(1400, 800)

        self._curves: list[pg.PlotDataItem] = []
        self._emg_df: pd.DataFrame | None = None

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

        self.btn_all_on = QPushButton("全显")
        self.btn_all_on.clicked.connect(lambda: self._set_all(True))
        bar.addWidget(self.btn_all_on)

        self.btn_all_off = QPushButton("全隐")
        self.btn_all_off.clicked.connect(lambda: self._set_all(False))
        bar.addWidget(self.btn_all_off)

        bar.addStretch()
        root.addLayout(bar)

        # 左树 + 右图
        splitter = QSplitter(Qt.Horizontal)

        # 左: 通道勾选树
        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.itemChanged.connect(self._on_item_changed)
        tree_layout.addWidget(self.tree)
        splitter.addWidget(tree_panel)

        # 右: 绘图区
        pg.setConfigOptions(antialias=ANTIALIAS)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "时间", units="s")
        self.plot.setLabel("left", "EMG (V)")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend(offset=(10, 10))
        splitter.addWidget(self.plot)

        splitter.setSizes([250, 1150])
        root.addWidget(splitter)

    # ── 数据加载 ──────────────────────────────────────
    def _load_trials(self):
        """加载当前条件下的 trial 列表"""
        self.cmb_trial.blockSignals(True)
        self.cmb_trial.clear()

        cond = self.cmb_cond.currentData()
        emg_dir = DATA_DIR / cond / "emg"
        if emg_dir.is_dir():
            files = sorted([f for f in os.listdir(emg_dir) if f.endswith(".csv")])
            for f in files:
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

        csv_path = DATA_DIR / cond / "emg" / fname
        if not csv_path.exists():
            return

        self.statusBar().showMessage(f"加载: {csv_path.name} ...")
        QApplication.processEvents()

        df = pd.read_csv(csv_path)
        self._emg_df = df
        self._plot_emg(df)

        self.statusBar().showMessage(
            f"{csv_path.name}  |  {len(df)} 行  |  {len(df.columns)-1} 通道"
        )

    # ── 绘图 ──────────────────────────────────────────
    def _plot_emg(self, df: pd.DataFrame):
        """绘制 EMG 各通道曲线"""
        # 清除旧曲线和图例
        self.plot.clear()
        legend = self.plot.plotItem.legend
        if legend is not None:
            legend.clear()
        self._curves.clear()

        # 清除旧树
        self.tree.blockSignals(True)
        self.tree.clear()

        t = df["Header"].values
        channels = [c for c in df.columns if c != "Header"]
        n = len(channels)

        # 创建树顶层组
        top = QTreeWidgetItem(self.tree, [f"EMG 通道 ({n})"])
        top.setCheckState(0, Qt.Checked)

        for i, ch in enumerate(channels):
            y = df[ch].values
            hue = i / n
            r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.9)
            color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
            pen = pg.mkPen(color, width=1.2)

            curve = setup_curve(self.plot.plot(t, y, name=ch, pen=pen))
            self._curves.append(curve)

            # 添加树叶子
            label = f"{ch} ({EMG_LABELS.get(ch, ch)})"
            leaf = QTreeWidgetItem(top, [label])
            leaf.setCheckState(0, Qt.Checked)
            leaf.setData(0, Qt.UserRole, i)
            leaf.setForeground(0, QColor(color))

        self.tree.expandAll()
        self.tree.blockSignals(False)

    # ── 交互 ──────────────────────────────────────────
    def _on_item_changed(self, item: QTreeWidgetItem, col: int):
        """树勾选联动: 顶层级联, 叶子切换曲线显隐"""
        idx = item.data(0, Qt.UserRole)
        if idx is not None:
            # 叶子节点 → 切换对应曲线
            self._curves[idx].setVisible(item.checkState(0) == Qt.Checked)
        else:
            # 顶层/组节点 → 级联到所有子节点
            state = item.checkState(0)
            self.tree.blockSignals(True)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, state)
                cidx = child.data(0, Qt.UserRole)
                if cidx is not None:
                    self._curves[cidx].setVisible(state == Qt.Checked)
            self.tree.blockSignals(False)

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
        for curve in self._curves:
            curve.setVisible(visible)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    viewer = EMGViewer()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
