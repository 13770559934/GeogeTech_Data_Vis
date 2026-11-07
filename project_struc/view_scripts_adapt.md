# `view_*` 脚本改造：适配全量 CSV 数据集

> 本文记录「把 `view_emg.py` / `view_emg_imu.py` / `view_emg_envelope.py` 从单 subject 镜像
> 改造成能浏览全量 21 个受试者」这件事的背景、实测事实、设计决策、API 契约和验证方法。
>
> 前置文档：[`transfer_mat2csv.md`](transfer_mat2csv.md)（`.mat` → `.csv` 的转换说明）。
>
> 后续新增功能（步态周期可视化）：[`gait_cycle_view.md`](gait_cycle_view.md)。
>
> 状态：**已完成**。

---

## 1. 为什么要改

### 1.1 改之前的处境

`data_GeorgiaTech_csv/` 是全量数据集（21 个受试者 × 4 种运动条件 × 13 类传感器，
约 36,000 个 CSV / 130 GB），目录层级是：

```
data_GeorgiaTech_csv/<SUBJECT>/<condition>/<sensor>/<trial>.csv
```

但三个可视化脚本只认**旧的单 subject 布局**：

```python
# view_emg.py:27 / view_emg_imu.py:27 / view_emg_envelope.py:27
DATA_DIR = Path(__file__).parent          # ← 仓库根目录
```

也就是 `<repo>/<condition>/<sensor>/<trial>.csv`。仓库根目录那套
`levelground/ ramp/ stair/ treadmill/` 其实是 **AB06 一人的残缺镜像**（README 里写明
「受试者: AB06」），而且是上一轮转换的遗留物：

| 目录 | 文件数 / 应有 |
|---|---|
| `levelground/emg/` | 27 / 30 |
| `levelground/conditions/` | 1 / 30（且这一个格式是坏的） |
| 其余 subject | **完全没有** |

结果就是：**其余 20 个受试者的数据虽然已经转好了，但脚本根本看不到。**

### 1.2 改完之后

```
受试者: [AB06 ▾]   条件: [平地行走 (levelground) ▾]   Trial: [levelground_ccw_fast_01_01 ▾]
```

选受试者 → 选地形 → 选 trial → 出图，**和以前完全一样的图**。
绘图代码、配色、树形勾选联动、滚轮缩放拖拽，一行都没动。

---

## 2. 实测到的事实

下面这些不是推断，是对着磁盘实测的（AB06–AB09 全部 16 个 subject×condition 组合）：

| # | 事实 | 为什么重要 |
|---|---|---|
| 1 | 新布局是 `<root>/<SUBJECT>/<cond>/<sensor>/<trial>.csv`，**没有 date 层** | 原始 `.mat` 是 `<subj>/<date>/<cond>/<sensor>/`，转换时把 date 去掉了（每个 subject 只有一个日期目录） |
| 2 | 每个 condition 下 **13 个传感器目录全齐**：`conditions emg emg_envelope fp gcLeft gcRight gon id ik ik_offset imu jp markers` | 目录列表可以写死，不用探测 |
| 3 | 同一个 trial 在 13 个传感器目录下**文件名完全相同**（集合逐个比对，`identical_across_13_dirs=True`） | `view_emg_imu` 的 emg/imu 配对逻辑可以直接照搬 |
| 4 | 21 个源 subject **全部**有 4 个 condition（`static` 没转） | 条件下拉框不用按 subject 特判 |
| 5 | 各传感器共享同一条**绝对时间轴**，`Header[0]` 相同（例：`14.42` s），只是采样率不同（emg/fp/gon 1000 Hz，其余 200 Hz） | 对齐靠 `Header`，不靠行号 |
| 6 | 文件名全部小写（源 `.mat` 有大写的，转换时 `.lower()`）；subject 目录大写 `ABxx`；`gcLeft`/`gcRight` 是驼峰 | 大小写敏感匹配时要小心 |
| 7 | trial 数**不齐**：levelground 25~36、ramp 60~70、stair 35~45、treadmill 7~9；还夹着 `treadmill_04_2_01` 这种不合命名规律的 | **不要解析文件名**，当不透明字符串用 |
| 8 | 旧根目录镜像 = 新 `AB06` 的**真子集**（emg 27/61/41/8，逐字节一致） | 可以用它做改造前后的等价性校验 |
| 9 | 转换脚本**还在后台跑**，subject 会陆续出现（改造期间 AB06→AB11 已完成） | subject 列表要**动态扫描**，不能写死 |

---

## 3. 设计决策

### 3.1 为什么抽一个公共模块 `gt_dataset.py`

三个脚本要用的东西**完全一样**：`DATA_DIR` 怎么定、subject 有哪些、某个 subject 下有哪些
condition、某个目录下有哪些 trial、某个文件在哪。

一开始可以每个脚本各抄一份，但那样路径规则就有三份拷贝 —— 以后加传感器、改布局，得同时改三处，
漏一处就是 bug。而且这三个脚本本来就是同一套 UI 模式（顶栏三个下拉 + 左侧树 + 右侧图）。

所以抽一个模块，**路径规则只写一遍**：

```
gt_dataset.py          路径解析 / 目录发现 / CLI 参数
   ↑        ↑        ↑
view_emg.py  view_emg_imu.py  view_emg_envelope.py
```

模块本身只回答两个问题：**路径怎么拼**、**目录里有什么**。不碰数据，不 import 任何 GUI 库。

### 3.2 为什么布局要「双模探测」而不是只认新的

用户明确要求**保留旧的根目录镜像作为 fallback**。理由也站得住：

- 旧的 `levelground/ ramp/ stair/ treadmill/` 是 AB06 的镜像，老的调用习惯（`cd levelground` 看图）
  不能因为这次改造就断掉；
- 新目录 `data_GeorgiaTech_csv/` 是在 `.gitignore` 里的，一台新机器 clone 下来可能只有旧的；
- 万一以后想用 `data_GeorgiaTech_csv/AB06` 单独看一个人，也还是得能指过去。

所以 `Dataset` 在构造时探一次布局：

| 布局 | 判定条件 | subject 层 |
|---|---|---|
| `new` | root 下有 `ABxx/<condition>/` | 有 |
| `legacy` | root 下有 `<condition>/` | 没有，subject 固定 `AB06` |
| 都没 | — | 抛 `FileNotFoundError`（带可读提示） |

探测顺序是「先新后旧」。`detect_root()` 的完整优先级：

1. `--data-dir` 命令行参数
2. 环境变量 `GEOGETECH_DATA_DIR`
3. `<repo>/data_GeorgiaTech_csv`（且确实是新布局时）
4. `<repo>` 本身（当成旧布局）

### 3.3 为什么 subject / condition 列表是「动态派生」而不是写死

三个理由：

1. **转换还在后台跑。** 21 个 subject 一个一个出，写死 `["AB06"..."AB28"]` 会让用户选到还没转完的
   subject，然后看到空列表 —— 体验很差。动态扫描则只列出**已经能用**的。
2. **trial 数和 condition 数不整齐。** 有 subject 缺某个 condition、某个 condition 的 trial 只有 7 个，
   写死必然对不上。
3. **condition 列表要按 subject 过滤。** `conditions_for(subject, *sensors)` 要求那些传感器目录
   **存在且非空**才把这个 condition 列出来 —— 于是「正在转换到一半的 subject」不会显示出
   一堆点开是空的选项。

同理 `Dataset` **不做缓存**，每次都现扫目录 —— 后台转换出新文件，重扫一次就能看到。
（代价是每次切 subject 要几次 `glob`，实测在毫秒级，可以接受。）

### 3.4 为什么 `trials()` 用「交集」语义

`view_emg_imu.py` 原来的逻辑是「列 `emg/` 里的文件，且 `imu/` 里也有同名文件的才算」。
这个逻辑不能丢 —— 旧镜像里 `ramp/emg` 有 61 个文件、`ramp/imu` 只有 60 个，
交集正好把那个「只有 EMG 没有 IMU」的 trial 排除掉。

所以 `Dataset.trials(subject, condition, *sensors)`：

- 只给一个传感器 → 该目录下全部 `.csv`
- 给多个传感器 → 各目录的**交集**

一个方法同时覆盖两种语义，`view_emg_imu` 直接调
`trials(subject, cond, "emg", "imu")` 就等价于原来的配对逻辑。

### 3.5 为什么「文件名不解析」

数据集里有 `treadmill_04_2_01.csv`（多一段），trial 数也每个 subject 都不一样。
一旦按 `_{方向}_{速度}_{trial}_{segment}` 去解析，就得处理各种例外。
**文件名只当不透明字符串**，`sort()` 一下就完事 —— 这是 `transfer_mat2csv.md` 第 8 节
就已经定下的原则，这里继续遵守。

---

## 4. `gt_dataset.py` 的 API 契约

```python
from gt_dataset import Dataset, detect_root, resolve_dataset, add_data_args
```

### 4.1 `Dataset`

| 成员 | 签名 | 说明 |
|---|---|---|
| `Dataset(root)` | `(Path \| str)` | 构造时自动探测布局 |
| `.root` | `Path` | 数据根目录（已 resolve） |
| `.layout` | `"new" \| "legacy"` | 探测到的布局 |
| `.subjects` | `-> list[str]` | 所有可用受试者（new：扫 `AB\d+`；legacy：`["AB06"]`） |
| `.conditions_for(subject, *sensors)` | `-> list[str]` | 该 subject 下、传感器目录**存在且非空**的 condition，固定序 `levelground→ramp→stair→treadmill` |
| `.condition_dir(subject, condition)` | `-> Path` | `<root>/<subject>/<cond>`（legacy 少一层） |
| `.sensor_dir(subject, condition, sensor)` | `-> Path` | `<root>/<subject>/<cond>/<sensor>`（legacy 少一层） |
| `.has(subject, condition, sensor)` | `-> bool` | 传感器目录是否存在 |
| `.trial_path(subject, condition, sensor, trial)` | `-> Path` | 完整文件路径 |
| `.trials(subject, condition, *sensors)` | `-> list[str]` | 排序后的 trial 文件名；多传感器时取交集 |

所有方法都对 `subject` / `condition` 为空做了兜底（返回空列表 / `False`），
所以 UI 在「一个都没扫到」的状态下不会崩。

### 4.2 根目录定位与 CLI

| 函数 | 说明 |
|---|---|
| `detect_root(explicit=None)` | 按 3.2 的优先级定位根目录 |
| `resolve_dataset(args)` | `Dataset(detect_root(args.data_dir))` |
| `add_data_args(parser)` | 往 parser 上挂 `--data-dir` / `--subject` / `--condition` / `--trial` |

### 4.3 诊断入口

```bash
uv run python gt_dataset.py
```

打印根目录、探测到的布局、受试者列表，以及每个 subject × condition 的
`emg` / `emg∩imu` / `envelope` 文件数。既能人工确认数据集状态，也是冒烟测试。

---

## 5. 三个脚本的改动清单

三个脚本改法完全一致，模式是「**顶栏加一个受试者下拉框** + **路径改走 `Dataset`**」。

### 5.1 共同改动

| 位置 | 改前 | 改后 |
|---|---|---|
| 模块顶部 | `DATA_DIR = Path(__file__).parent`、`CONDITIONS`、`CONDITION_LABELS` 各抄一份 | 从 `gt_dataset` import，`DATA_DIR` 删除 |
| 顶栏 | `条件: [▾] Trial: [▾]` | `受试者: [▾] 条件: [▾] Trial: [▾]` |
| `__init__` | 无参 | `__init__(self, dataset, subject=None, condition=None, trial=None)` |
| 新增 `_populate_subjects()` | — | 扫 `dataset.subjects` 填受试者下拉 |
| 新增 `_reload_conditions()` | — | 按当前 subject 刷条件下拉（保持用户已选条件） |
| `_load_trials()` | `os.listdir(DATA_DIR/cond/emg)` | `dataset.trials(subject, cond, *REQUIRED_SENSORS)` |
| `_on_trial_changed()` | `DATA_DIR / cond / "emg" / fname` | `dataset.trial_path(subject, cond, "emg", fname)` |
| 新增 `_on_subject_changed()` | — | 改标题 → 刷 condition → 刷 trial |
| 窗口标题 | 硬编码 `"AB06 EMG 可视化"` | 随 subject 变 |
| `main()` | 直接构造窗口 | `argparse` + `resolve_dataset(args)` + 预选 |

**联动链**：`受试者` → 重刷 `条件` → 重刷 `Trial` → 重新绘图。
每次重刷都 `blockSignals` + 手动触发一次 `_on_trial_changed()`，避免重复加载/重复绘图。

**预选**：`--subject` / `--condition` / `--trial` 在启动时直接落到对应项；
找不到（subject 不存在、该 subject 没这个 condition、trial 名对不上）就**回落到第一个可用项**，
不报错 —— 转换还在后台跑的时候这是最常见的情况。

### 5.2 各脚本的差异

| 脚本 | `REQUIRED_SENSORS` | trial 列表语义 | 特殊路径 |
|---|---|---|---|
| `view_emg.py` | `("emg",)` | `emg/` 全部 | — |
| `view_emg_imu.py` | `("emg", "imu")` | **emg ∩ imu**（等价于原 `view_emg_imu.py:208-211` 的配对逻辑） | — |
| `view_emg_envelope.py` | `("emg",)` | `emg/` 全部 | 包络取 `sensor_dir(subject, cond, "emg_envelope")`（**与 `emg` 同级**，不是 `emg/` 里面）；缺文件时照旧降级成下图空白 |

### 5.3 刻意**没有**改的部分

用户的要求是「只做适配，行为完全不变」。下面这些**明确不改**（`transfer_mat2csv.md` 第 11.6 节
把它们列为「顺带可以做的」，留作以后的可选项）：

- **时间轴是否归零** —— `view_emg.py` 和 `view_emg_envelope.py` 的 X 轴继续显示绝对时间戳
  （起点是 `14.42` 这种），`view_emg_imu.py` 继续归零。保持原样，方便和历史截图对照。
- **不加「刷新」按钮** —— subject 列表在启动时扫一次；转换中途新出现的 subject 需要重启程序才能选到。
- **`view_emg_imu.py` 不加归一化 / 双 Y 轴** —— 11 通道 EMG 叠加 IMU 还是那么挤，维持原状。
- **`view_emg_envelope.py` 不加「原始 vs 包络」差值视图** —— 维持上下两图对照。
- **绘图代码**（`setup_curve`、11 通道配色、树形勾选联动、状态栏文案）**一行未动**。

> `levelground/visualize_IMU_EMG.py` 也**没有**迁移 —— 它埋在 gitignore 的数据目录里，
> 而且不是 `view*` 脚本。它在旧布局下仍然能跑。

---

## 6. 边界情况与坑

| 坑 | 处理 |
|---|---|
| 转换还在后台跑，subject 目录存在但内容不全 | `conditions_for()` 要求传感器目录**存在且非空**才列出该 condition；subject 目录若一个 condition 都没有则不出现在列表里 |
| 同一 subject 的 trial 数不齐（levelground 25~36） | 列表现扫现用，不写死 |
| 文件名不合命名规律（`treadmill_04_2_01`） | 当不透明字符串，不解析 |
| 旧镜像 `ramp/` 的 emg 有 61 个、imu 只有 60 个 | `trials(..., "emg", "imu")` 取交集，正好排掉那个没 IMU 的 |
| 旧镜像 `conditions/` 只有一个文件且格式是坏的 | 本次不碰 `conditions/`，三个脚本都不读它 |
| 旧镜像的 `emg_envelope/` 也不全（ramp 59/61） | `view_emg_envelope` 缺包络文件时照旧降级（`env_df=None`，下图空白） |
| 转换过程中文件名可能正在写 | `pd.read_csv` 读不到就抛异常；这是既有行为，未改 |
| 大文件 —— treadmill 的 emg 单文件 33 MB | 既有行为（一次性 `read_csv` 整个文件），未改 |
| `gcLeft` / `gcRight` 是驼峰，其余传感器目录全小写 | 目录名写死在 `SENSORS` 常量里，不做 `.lower()` 猜测 |
| 控制台是 GBK | `gt_dataset.py` 给 stdout/stderr 加了 `errors="replace"`，与 `convert_mat_to_csv.py` 一致 |

---

## 7. 怎么用

```bash
# 最简单：不带参数，自动进 AB06 / 平地行走
uv run python view_emg.py

# 直接落到指定的受试者和地形
uv run python view_emg.py --subject AB12 --condition ramp

# 连 trial 一起指定
uv run python view_emg_imu.py --subject AB12 --condition treadmill --trial treadmill_03_01

# 指定别的数据根目录（也会自动识别新旧两种布局）
uv run python view_emg_envelope.py --data-dir D:\somewhere\data_GeorgiaTech_csv

# 用环境变量指定根目录
set GEOGETECH_DATA_DIR=E:\GeogeTech_Data_Vis\data_GeorgiaTech_csv

# 看数据集结构概览
uv run python gt_dataset.py
```

---

## 8. 验证方法与实测结果

### 8.1 已做的验证

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | 模块能正确探测布局、列出 subject/condition/trial | `uv run python gt_dataset.py` | `layout=new`，列出 AB06…AB11（转换进行中），每个 subject 4 个 condition，trial 数与转换报告一致 ✅ |
| 2 | 三个脚本都能构造出完整 UI | `QT_QPA_PLATFORM=offscreen` 离屏实例化 | subject/condition/trial 三个下拉都有值，标题随 subject 变 ✅ |
| 3 | 图确实画出来了 | 离屏实例化后数曲线对象 | EMG 11 条、EMG+IMU 11 条 + IMU 下拉 24 项、EMG+包络 raw 11 条 + env 11 条 ✅ |
| 4 | 切受试者会重建 condition 和 trial 列表 | 逐个 `setCurrentIndex` 遍历 | AB06→AB11 各自 levelground 30/31/36/32/32/32 个 trial，条件保持不变 ✅ |
| 5 | 切地形会重建 trial 列表 | 切 AB08 的 4 个条件 | 36 / 60 / 35 / 7，状态栏行数跟着变 ✅ |
| 6 | 命令行预选生效 | `--subject/--condition/--trial` | 精确落到 `AB09 / 坡道行走 / ramp_3_l_01_02` ✅ |
| 7 | 预选不到时优雅回落 | 不存在的 subject / trial | 回落到 AB06 / 第一个 trial，不报错 ✅ |
| 8 | 大小写不敏感 | `--subject ab09` | 命中 `AB09` ✅ |
| 9 | 所有配对文件的路径真实存在 | 遍历 870 个 trial × emg/imu 断言 `Path.exists()` | 全通过 ✅ |
| 10 | 新旧布局数据一致 | `cmp` 旧镜像 `levelground/emg/*.csv` vs 新 `AB06/levelground/emg/*.csv` | 27/27 逐字节一致 ✅ |
| 11 | 旧布局 fallback 可用 | `gt_dataset.py --data-dir .` + 离屏实例化 | 识别为 `legacy`，subject 退化为单个 `AB06`，27 个 trial，出图正常 ✅ |

### 8.2 怎么自己复验

```bash
# 数据集结构
uv run python gt_dataset.py

# 离屏冒烟：三个脚本各来一遍
QT_QPA_PLATFORM=offscreen uv run python -c "
import sys
from PySide6.QtWidgets import QApplication
from gt_dataset import detect_root, Dataset
from view_emg import EMGViewer
from view_emg_imu import EMGIMUViewer
from view_emg_envelope import EMGEnvelopeViewer
app = QApplication(sys.argv[:1])
ds = Dataset(detect_root())
for cls in (EMGViewer, EMGIMUViewer, EMGEnvelopeViewer):
    v = cls(ds)
    print(cls.__name__, v.windowTitle(), v.cmb_trial.count(), 'trials')
"

# 路径交叉核对
uv run python -c "
from gt_dataset import detect_root, Dataset
ds = Dataset(detect_root())
for s in ds.subjects:
    for c in ds.conditions_for(s, 'emg', 'imu'):
        for f in ds.trials(s, c, 'emg', 'imu'):
            assert ds.trial_path(s, c, 'emg', f).exists()
print('OK')
"

# 旧镜像 fallback
uv run python gt_dataset.py --data-dir .

# 新旧数据逐字节比对
cmp levelground/emg/levelground_ccw_fast_01_01.csv \
    data_GeorgiaTech_csv/AB06/levelground/emg/levelground_ccw_fast_01_01.csv && echo IDENTICAL
```

### 8.3 人工过一遍界面

```bash
uv run python view_emg.py
```

逐项确认：受试者下拉能切、条件下拉跟着变、Trial 列表跟着变；左侧树的勾选联动、全显/全隐、
滚轮缩放、拖拽平移都跟改造前一致；状态栏的行数和通道数正确。

---

## 9. 相关文件

| 文件 | 说明 |
|---|---|
| `gt_dataset.py` | **新增** —— 路径解析 / 目录发现 / CLI 参数 / 诊断入口 |
| `view_emg.py` | 改 —— 接入 `gt_dataset`，加受试者下拉 |
| `view_emg_imu.py` | 改 —— 同上，emg∩imu 交集走 `Dataset.trials()`；另加步态周期可视化（见 [`gait_cycle_view.md`](gait_cycle_view.md)） |
| `view_emg_envelope.py` | 改 —— 同上，包络路径走 `Dataset.sensor_dir(..., "emg_envelope")` |
| `gt_gait.py` | **新增** —— 步态事件解析 + pyqtgraph 叠加层（见 [`gait_cycle_view.md`](gait_cycle_view.md)） |
| `project_struc/transfer_mat2csv.md` | 转换说明（本改造的前置文档） |
| `project_struc/gait_cycle_view.md` | 步态周期可视化（后续新增功能的文档） |
| `convert_mat_to_csv.py` | **未改动** —— 数据生产端 |
