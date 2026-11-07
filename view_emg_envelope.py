"""
Georgia Tech 数据集 EMG 原始信号 + ISEK 包络 对比可视化

使用 PySide6 + pyqtgraph，支持：
  - 选择受试者 (AB06 … AB28) 与运动条件 (levelground/ramp/stair/treadmill)
  - 上图: 原始 EMG 信号，下图: ISEK 包络信号
  - 11 通道叠加显示，左侧树勾选显隐（两图联动）
  - X 轴联动，鼠标滚轮缩放、拖拽平移

命令行:
    uv run python view_emg_envelope.py
    uv run python view_emg_envelope.py --subject AB12 --condition ramp
    uv run python view_emg_envelope.py --data-dir D:\\path\\to\\data_GeorgiaTech_csv
"""

import sys
import argparse
import colorsys

import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLabel, QSplitter, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from gt_dataset import (
    CONDITION_LABELS, Dataset, add_data_args, resolve_dataset,
)

# 11 个 EMG 通道的中文名
EMG_LABELS = {
    "gastrocmed": "腓肠肌内侧头",
    "tibialisanterior": "胫骨前肌",
    "soleus": "比目鱼肌",
    "vastusmedialis": "股内侧肌",
    "vastuslateralis": "股外侧肌",
    "rectusfemoris": "股直肌",
    "bicepsfemoris": "股二头肌",
    "semitendinosus": "半腱肌",
    "gracilis": "股薄肌",
    "gluteusmedius": "臀中肌",
    "rightexternaloblique": "右腹外斜肌",
}

# ── 渲染性能 ────────────────────────────────────────
# 抗锯齿: pyqtgraph 开启后会走 QPainterPath 描边, 而不是 drawLines 快路径。
# 一条 1.5 万点的曲线单次描边约 176ms, 上下两图共 22 条曲线重绘一次要 8 秒以上,
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


class EMGEnvelopeViewer(QMainWindow):
    """EMG 原始信号 + 包络 对比可视化主窗口"""

    #: 本视图依赖的传感器；trial 列表只列出 emg 目录里存在的文件
    #: （emg_envelope 是派生目录，缺失时下图留空）
    REQUIRED_SENSORS = ("emg",)

    def __init__(self, dataset: Dataset, subject: str | None = None,
                 condition: str | None = None, trial: str | None = None):
        super().__init__()
        self.dataset = dataset
        self.resize(1400, 900)

        self._raw_curves: list[pg.PlotDataItem] = []
        self._env_curves: list[pg.PlotDataItem] = []

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

        self.btn_all_on = QPushButton("全显")
        self.btn_all_on.clicked.connect(lambda: self._set_all(True))
        bar.addWidget(self.btn_all_on)

        self.btn_all_off = QPushButton("全隐")
        self.btn_all_off.clicked.connect(lambda: self._set_all(False))
        bar.addWidget(self.btn_all_off)

        bar.addStretch()
        root.addLayout(bar)

        # 左树 + 右双图
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

        # 右: 上下双图
        pg.setConfigOptions(antialias=ANTIALIAS)
        plot_splitter = QSplitter(Qt.Vertical)

        # 上图: 原始 EMG
        self.plot_raw = pg.PlotWidget()
        self.plot_raw.setLabel("bottom", "时间", units="s")
        self.plot_raw.setLabel("left", "原始 EMG (V)")
        self.plot_raw.showGrid(x=True, y=True, alpha=0.3)
        self.plot_raw.addLegend(offset=(10, 10))
        plot_splitter.addWidget(self.plot_raw)

        # 下图: 包络
        self.plot_env = pg.PlotWidget()
        self.plot_env.setLabel("bottom", "时间", units="s")
        self.plot_env.setLabel("left", "包络 (V)")
        self.plot_env.showGrid(x=True, y=True, alpha=0.3)
        self.plot_env.addLegend(offset=(10, 10))
        plot_splitter.addWidget(self.plot_env)

        # X 轴联动
        self.plot_env.setXLink(self.plot_raw)

        plot_splitter.setSizes([450, 450])
        splitter.addWidget(plot_splitter)

        splitter.setSizes([250, 1150])
        root.addWidget(splitter)

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
        """加载当前受试者 + 条件下的 trial 列表"""
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
        self.setWindowTitle(f"{subject} EMG 原始信号 / ISEK 包络 对比")

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

        raw_path = self.dataset.trial_path(subject, cond, "emg", fname)
        env_path = self.dataset.trial_path(subject, cond, "emg_envelope", fname)
        if not raw_path.exists():
            return

        self.statusBar().showMessage(f"加载: {raw_path.name} ...")
        QApplication.processEvents()

        raw_df = pd.read_csv(raw_path)
        if env_path.exists():
            env_df = pd.read_csv(env_path)
        else:
            env_df = None

        self._plot(raw_df, env_df)

        env_info = f"  |  包络: 有" if env_df is not None else "  |  包络: 无"
        self.statusBar().showMessage(
            f"{raw_path.name}  |  {len(raw_df)} 行  |  {len(raw_df.columns)-1} 通道{env_info}"
        )

    # ── 绘图 ──────────────────────────────────────────
    def _plot(self, raw_df: pd.DataFrame, env_df: pd.DataFrame | None):
        """绘制原始 EMG 和包络曲线"""
        # 清除旧曲线和图例
        for plot, curves in [(self.plot_raw, self._raw_curves),
                             (self.plot_env, self._env_curves)]:
            plot.clear()
            legend = plot.plotItem.legend
            if legend is not None:
                legend.clear()
            curves.clear()

        # 清除旧树
        self.tree.blockSignals(True)
        self.tree.clear()

        t = raw_df["Header"].values
        channels = [c for c in raw_df.columns if c != "Header"]
        n = len(channels)

        # 创建树顶层组
        top = QTreeWidgetItem(self.tree, [f"EMG 通道 ({n})"])
        top.setCheckState(0, Qt.Checked)

        for i, ch in enumerate(channels):
            hue = i / n
            r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.9)
            color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
            pen = pg.mkPen(color, width=1.2)

            # 上图: 原始信号
            y_raw = raw_df[ch].values
            curve_raw = setup_curve(self.plot_raw.plot(t, y_raw, name=ch, pen=pen))
            self._raw_curves.append(curve_raw)

            # 下图: 包络
            if env_df is not None and ch in env_df.columns:
                y_env = env_df[ch].values
                t_env = env_df["Header"].values
                curve_env = setup_curve(self.plot_env.plot(t_env, y_env, name=ch, pen=pen))
                self._env_curves.append(curve_env)
            else:
                self._env_curves.append(None)

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
            visible = item.checkState(0) == Qt.Checked
            self._raw_curves[idx].setVisible(visible)
            if self._env_curves[idx] is not None:
                self._env_curves[idx].setVisible(visible)
        else:
            # 顶层/组节点 → 级联到所有子节点
            state = item.checkState(0)
            self.tree.blockSignals(True)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, state)
                cidx = child.data(0, Qt.UserRole)
                if cidx is not None:
                    visible = state == Qt.Checked
                    self._raw_curves[cidx].setVisible(visible)
                    if self._env_curves[cidx] is not None:
                        self._env_curves[cidx].setVisible(visible)
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
        for curve in self._raw_curves:
            curve.setVisible(visible)
        for curve in self._env_curves:
            if curve is not None:
                curve.setVisible(visible)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Georgia Tech 数据集 EMG 原始信号 / ISEK 包络 对比")
    add_data_args(parser)
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    viewer = EMGEnvelopeViewer(dataset, subject=args.subject,
                               condition=args.condition, trial=args.trial)
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
