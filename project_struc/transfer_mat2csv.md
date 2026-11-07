# MAT → CSV 转换：把 `data_GeorgiaTech/` 全量转成 `data_GeorgiaTech_csv/`

> 本文件是「转换需求 + 数据结构认知 + 格式规范 + 踩坑记录」的完整说明。
> 状态：**已完成**（脚本已实现并跑完全量），下一阶段的软件改造见文末第 11 节。

---

## 1. 目标与背景

### 1.1 为什么需要转

- `data_GeorgiaTech/` 是本项目的原始数据集，全部以 MATLAB `.mat` 存储（**50 GB / 21 个 subject / 35,988 个 trial 文件**）。
- 仓库里的三个可视化脚本 —— `view_emg.py`、`view_emg_imu.py`、`view_emg_envelope.py` —— **只读 CSV**，
  而且路径是写死的：`DATA_DIR = Path(__file__).parent`（`view_emg_imu.py:27`），
  约定 `<repo>/<condition>/<modality>/<trial>.csv`。
- 仓库根目录的 `levelground/ ramp/ stair/ treadmill/` 其实**只是 AB06 一个 subject 的 CSV 镜像**
  （`README.md` 开头写明「受试者: AB06 | 采集日期: 2018年10月9日」），而且是上一轮转换遗留的**残缺产物**：

  | 目录 | 文件数 / 应有 | 说明 |
  |---|---|---|
  | `levelground/emg/` | 27 / 30 | 缺 `ccw_fast_03_01`、`ccw_slow_02_01`、`cw_normal_05_01` |
  | `levelground/conditions/` | 1 / 30 | 且这一个文件格式是坏的（见 5.3） |
  | `levelground/markers/` `fp/` | 28 / 30 | 各缺 2 个 |
  | 其余 subject | — | **完全没有** |

### 1.2 目标

把全部 21 个 subject、12 类传感器的 `.mat` 统一转成 CSV，输出到

```
data_GeorgiaTech_csv/<SUBJECT>/<condition>/<sensor>/<trial>.csv
```

目录层级与根目录那套参考镜像完全一致，之后 `view_*` 脚本就能直接吃任意 subject 的数据。

---

## 2. 原始数据结构

### 2.1 目录层级

```
data_GeorgiaTech/
├── README.txt              # 数据集官方说明（含引用要求）
├── SubjectInfo.mat         # 受试者信息表（不是 trial）
├── AB06/ … AB28/           # 21 个 subject 目录
└── 每个 subject/
    ├── <date>/             # 采集日期目录，每个 subject 只有一个
    │   ├── levelground/    # 4 个运动条件
    │   │   ├── conditions/ # 12 个传感器目录（下面再一层是文件）
    │   │   ├── emg/
    │   │   ├── emg_csv/    # ← 数据集自带的 CSV 镜像（emg/gon/imu 三个有）
    │   │   ├── fp/
    │   │   ├── gcLeft/
    │   │   ├── gcRight/
    │   │   ├── gon/
    │   │   ├── gon_csv/
    │   │   ├── id/
    │   │   ├── ik/
    │   │   ├── ik_offset/
    │   │   ├── imu/
    │   │   ├── imu_csv/
    │   │   ├── jp/
    │   │   └── markers/
    │   ├── ramp/ …
    │   ├── stair/ …
    │   ├── treadmill/ …
    │   └── static/         # ← 第 5 个 mode，只有 ik / ik_offset / markers
    └── osimxml/            # OpenSim 模型与配置（ABXX.osim, idsetup.xml, iksetup.xml）
```

**层级是 `<subject>/<date>/<mode>/<sensor>/<file>`，5 层，文件平铺，没有 per-trial 子目录。**
输出时**不保留 date 这一层**（每个 subject 只有一个日期目录）。

### 2.2 21 个 subject 与日期目录

| Subject | 日期目录 | Subject | 日期目录 |
|---|---|---|---|
| AB06 | `10_09_18` | AB17 | `11_11_2018` |
| AB07 | `10_14_18` | AB18 | `11_14_2018` |
| AB08 | `10_21_2018` | AB19 | `11_18_2018` |
| AB09 | `10_21_2018` | AB20 | `11_18_2018` |
| AB10 | `10_28_2018` | AB21 | `01_27_2019` |
| AB11 | `10_28_2018` | AB23 | `11_25_2018` |
| AB12 | `11_04_2018` | AB24 | `12_02_2018` |
| AB13 | `11_04_2018` | AB25 | `01_20_2019` |
| AB14 | `11_25_2018` | AB27 | `02_24_2019` |
| AB15 | `11_07_2018` | AB28 | `03_02_2019` |
| AB16 | `11_11_2018` | | |

没有 AB01–AB05、AB22、AB26（论文写的是 22 人，实际这个副本只有 21 人）。

### 2.3 文件命名

| mode | 格式 | 数量级 |
|---|---|---|
| levelground | `levelground_{cw\|ccw}_{slow\|normal\|fast}_{trial}_{segment}.csv` | 30 |
| ramp | `ramp_{坡度1-6}_{l\|r}_{trial}_{segment}.csv` | 63 |
| stair | `stair_{楼梯1-4}_{l\|r}_{trial}_{segment}.csv` | 42 |
| treadmill | `treadmill_{trial}_{segment}.csv` | 8 |
| static | `static_01` / `static01` / `Static_01` | 1 |

trial 数**不齐**（AB15 levelground 25 个、AB17 22 个、AB08 36 个；AB07 多一个 `treadmill_04_2_01`），
所以脚本**不做任何文件名解析**，只按「目录里实际存在的文件」遍历。

### 2.4 数据集自带的 `_csv` 目录

`emg_csv/` `gon_csv/` `imu_csv/` 在**全部 21 个 subject × 4 个 mode** 都存在，且与对应 `.mat` 文件名
**一一对应**（8997 个 CSV，26 GB）。它们是数据集作者用 MATLAB `writetable` 导出的
（macOS 生成，所以行尾是 LF，float 文本是 MATLAB 的 15 位有效数字）。

本次转换**没有**直接复制它们，而是统一用 MATLAB 重新解码导出 —— 这样 12 个传感器的
文本格式完全一致；重新导出的结果与自带 `_csv` 数值一致（最大绝对差 5e-15 / 5e-14，
即 MATLAB 15 位文本截断的量级）。

---

## 3. `.mat` 内部结构

所有 `.mat` 都是 `MATLAB 5.0 MAT-file`（**v5，不是 v7.3/HDF5**），内部用 **zlib 压缩**存放。
真正的数据是 **MATLAB `table`**，被序列化成了 **MCOS opaque 对象**（`mxOPAQUE_CLASS`）。

顶层变量与 table 内容（实测，AB06 / levelground 抽样）：

| 传感器 | 顶层变量 | table 变量 | 行 × 列 | 列名 |
|---|---|---|---|---|
| conditions | `subject, file, trialType, leadingLegStart, leadingLegStop, labelError, trialStarts, trialEnds, turn, speed, labels` | **`labels`** | 3068 × 2 | `Header, Label` |
| emg | `data` | `data` | 15336 × 12 | `Header` + 11 块肌肉 |
| fp | `data` | `data` | 15336 × 64 | `Header` + 63 个力/力矩通道 |
| gcLeft / gcRight | `data` | `data` | 3068 × 3 | `Header, HeelStrike, ToeOff` |
| gon | `data` | `data` | 15336 × 6 | `Header` + 5 个角度 |
| id | `data` | `data` | 3068 × 24 | `Header` + 23 个力矩/力 |
| ik | `data` | `data` | 3068 × 24 | `Header` + 23 个角度 |
| ik_offset | `data` | `data` | 3068 × 24 | 同 `ik`（相对静态姿势） |
| imu | `data` | `data` | 3068 × 25 | `Header` + 4 部位 × {Accel, Gyro} × {X, Y, Z} |
| jp | `data` | `data` | 3068 × 24 | `Header` + 23 个功率 |
| markers | `data` | `data` | 3068 × 85 | `Header` + 28 标记点 × {x, y, z} |

要点：

- **`Header` 永远是第 0 列**，是时间戳（秒）。
- 只有 `conditions` 含一个字符串列 `Label`（`idle` / `stand` / …），其余全是数值。
- 采样率：emg / fp / gon = **1000 Hz**；ik / id / jp / imu / markers / gcLeft / gcRight / conditions = **200 Hz**。
- `gcLeft` / `gcRight` 的两列是 **0→100 的相位斜坡**，不是事件时间戳；
  提取规则见 [`gait_cycle_view.md`](gait_cycle_view.md) 第 2 节。
  转换本身不做任何解释，只把数字原样搬过来。

---

## 4. 为什么必须用 MATLAB 解码

把 MCOS opaque 解成真正的 table，Python 生态目前做不到。三条路都实测过：

| 方式 | 结果 |
|---|---|
| `scipy.io.loadmat` | ❌ 只返回 `MatlabOpaque` 占位符（`_TypeSystem='MCOS'`, `_Class='table'`），**一个数据都没有** |
| `pymatreader.read_mat` | ❌ 同样只有占位符；真实数据被原样塞进 `__function_workspace__`（uint8 裸字节） |
| **MATLAB R2024a** | ✅ `load` 出来就是真 table，`S.data` / `S.labels` 直接可用 |

本机装了 `C:\Program Files\MATLAB\R2024a`（license 有效），所以采用它解码。
脚本会自动找 MATLAB：环境变量 `MATLAB_EXE` → `PATH` 里的 `matlab` → `C:/Program Files/MATLAB/*/bin/matlab.exe`；
也可以用 `--matlab <路径>` 指定。

> 如果以后想摆脱 MATLAB 依赖，唯一出路是自己实现一个 MCOS table 解析器。
> 本脚本的流水线把「解码」和「写 CSV」分开了，替换解码后端不影响最终产物的字节格式。

---

## 5. 目标 CSV 格式规范

### 5.1 通用规则

- 一行表头 + N 行数据，逗号分隔。
- **第 0 列列名固定为 `Header`**，值是时间戳（秒）。
- 其余列全是数值通道；`view_*` 脚本用 `[c for c in df.columns if c != "Header"]` 取通道。
- 同一个 trial 在不同传感器下**文件名相同**，但采样率不同 → **行数不同**，靠 `Header` 时间戳对齐。
- 读取方式就是裸的 `pd.read_csv(path)`，没有 `index_col` / `usecols` / `names`。

### 5.2 行尾与 float 文本格式

| 来源 | 行尾 | float 文本 |
|---|---|---|
| 数据集自带 `_csv/`（MATLAB writetable，macOS） | LF | `0.0163010958904312`（15 位有效数字） |
| 根目录参考镜像（pandas to_csv，Windows） | **CRLF** | `0.016301095890431175`（最短往返 repr） |
| **本次输出（pandas to_csv，Windows）** | **CRLF** | **`0.016301095890431175`** |

本次输出刻意跟**根目录参考镜像**对齐，好处是可以做**逐字节 diff** 验收
（实测 244 个可比文件全部逐字节一致，见第 10 节）。

### 5.3 `conditions` 的格式说明

根目录参考镜像里的 `levelground/conditions/levelground_cw_slow_05_01.csv` 是**坏的**：
无表头、单列、第一行是字符串 `stand`，其余 5408 行是时间戳 —— 显然上一轮的转换脚本在这一步写错了。

本次输出的是**正确格式**：

```
Header,Label
14.42,idle
14.425,idle
...
```

即 `Header`（时间戳）+ `Label`（该时刻的运动状态标签）。

### 5.4 各传感器列数速查

| 传感器 | 列数 | 行数（levelground） | 采样率 |
|---|---|---|---|
| conditions | 2 | 3068 | 200 Hz |
| emg | 12 | 15336 | 1000 Hz |
| emg_envelope | 12 | 同 emg | 1000 Hz |
| fp | **19 / 46 / 64** | 15336 | 1000 Hz |
| gcLeft / gcRight | 3 | 3068 | 200 Hz |
| gon | 6 | 15336 | 1000 Hz |
| id | 24 | 3068 | 200 Hz |
| ik | 24 | 3068 | 200 Hz |
| ik_offset | 24 | 3068 | 200 Hz |
| imu | 25 | 3068 | 200 Hz |
| jp | 24 | 3068 | 200 Hz |
| markers | **76 ~ 202** | 3068 | 200 Hz |

⚠️ **`fp` 和 `markers` 的列数不是固定的**，这是数据本身的性质，不是转换问题：

- **`fp`**（地面反作用力）：列数取决于该 trial 用了几块力板。
  - **19 列** = 只有跑步机通道（无地面力板），150 个文件（全是 treadmill）
  - **46 列** = 跑步机 + 2 块力板，2203 个文件（主要是 ramp）
  - **64 列** = 跑步机 + 4 块力板 FP1/FP2/FP5/FP6，646 个文件（levelground）
- **`markers`**（光学标记点）：基础是 28 个标记点 × 3 轴 = 84 + Header = 85 列；
  但部分 subject 采集了**额外的躯干/上肢标记点**，列数从 76 一直到 202 不等。
  实测分布：85 列 1939 个、88 列 168 个、91 列 315 个、94 列 207 个、97 列 146 个 …

已用 MATLAB 直接核对过源 table：例如 `treadmill/fp/treadmill_01_01.mat` 本身就是 19 列、
`ramp/markers/ramp_4_l_01_01.mat` 本身就是 88 列 —— **产物与源 table 的列数逐个一致**。
所以读这两个传感器时**不要假设固定列数**，要按表头动态取列。

---

## 6. 输出目录规范

```
data_GeorgiaTech_csv/
├── _conversion_report/            # 每个 subject 一份 JSON 报告
│   ├── AB06.json … AB28.json
├── _work/                         # 中间态临时目录（每个 subject 处理完自动删）
└── <SUBJECT>/                     # AB06 … AB28
    ├── levelground/
    │   ├── conditions/  emg/  emg_envelope/  fp/  gcLeft/  gcRight/  gon/
    │   ├── id/  ik/  ik_offset/  imu/  jp/  markers/
    ├── ramp/   （同上 13 个子目录）
    ├── stair/  （同上）
    └── treadmill/ （同上）
```

- 文件名**统一转小写**（源数据里 AB25/AB27/AB28 用 `LevelGround_*`、`Ramp_*` 等大写形式，
  AB09 用大写的 `L`/`R` 方向标记；已确认同一目录内没有大小写冲突，转小写安全）。
- 每个 subject 约 1,700–1,850 个文件；全量 35,988 个 trial 文件 + 2,999 个包络 = **38,987 个 CSV，约 104.5 GB**
  （实测单个 subject ≈ 6.2 GB，是原始 `.mat` 的 2~3 倍 —— CSV 是未压缩文本）。

---

## 7. `emg_envelope` 派生规则

`view_emg_envelope.py` 需要 `emg_envelope/`，但**数据集里没有这个目录**，它是算出来的。
根目录参考镜像里有 136 个参考文件（levelground 30 / ramp 59 / stair 42 / treadmill 5），
可以拿来反推算法。

**采用算法（ISEP/ISEK 包络）**：

```
y = filtfilt(butter(4, fc=6Hz, 'low'), |x|)
```

- 先**全波整流** `|x|`
- 再过 **4 阶 Butterworth 低通，截止 6 Hz**
- 用 `filtfilt` 做**零相位**处理（不引入群延迟）
- **不归一化**，保留滤波下冲产生的少量负值（与参考文件一致）
- 采样率从 `Header` 时间戳的中位间隔实算，不写死

**实测吻合度**（对根目录 297 个可比通道）：

| 算法 | 平均 corr | 最小 corr | < 0.99 的通道数 |
|---|---|---|---|
| **butter o4 fc6 filtfilt（采用）** | **0.99540** | 0.90761 | **20 / 297** |
| savgol w201 | 0.99109 | 0.92240 | 66 / 297 |
| MA(100) 整流滑动平均 | 0.98995 | 0.92798 | 105 / 297 |
| butter o2 fc6 | 0.99374 | 0.90757 | 33 / 297 |
| sqrt(lp(x²)) 功率包络 | 0.97882 | 0.92067 | 296 / 297 |

29/30 个 trial 的整 trial 平均 corr ≥ 0.99；剩下 20 个偏低通道集中在
`levelground_cw_fast_05_01`（平均 0.9878）和 `levelground_ccw_slow_05_01`（平均 0.9908）两个 trial，
猜测上一轮生成参考包络的脚本对这两个文件用了别的参数。**没能 100% 复原参考生成器的原始算法**，
但对可视化用途完全够用。

---

## 8. 已知坑与处理方式

| 坑 | 处理方式 |
|---|---|
| `.mat` 里是 MCOS opaque table，scipy/pymatreader 都读不了 | 用 MATLAB R2024a 解码（见第 4 节） |
| 文件名大小写不统一（`LevelGround_*` / `Ramp_*` / 大写 `L`/`R`） | 输出时统一 `.lower()`；已确认无大小写冲突 |
| 每个 subject 的日期目录名格式不一（`10_09_18` vs `10_21_2018`） | 不解析日期，输出里不带 date 层 |
| 数据集还有第 5 个 mode `static/`（只有 ik/ik_offset/markers） | **本次不转**（根目录参考镜像也没有）；脚本代码路径已支持，需要时加 `--conditions static` 即可 |
| `SubjectInfo.mat` 是受试者信息表，不是 trial | 遍历时天然跳过（不在任何 mode 目录下） |
| trial 数不齐 / 有 `treadmill_04_2_01` 这种不合命名规律的 | 不解析文件名，按实际文件遍历 |
| `conditions` 的 table 变量叫 `labels` 而不是 `data` | MATLAB 端自动找第一个 `istable` 的变量 |
| `conditions` 含字符串列 `Label` | MATLAB 端拆成 `num_data` + `str_data`，Python 端还原成字符串列 |
| 根目录参考镜像里的 `conditions` 文件是坏的 | 本次输出正确的 `Header,Label` 两列格式（见 5.3） |
| 根目录参考镜像缺 6 个 emg / 29 个 conditions / 2 个 fp / 2 个 markers | 本次输出是完整的 100% |
| `emg_envelope` 数据集里没有 | 由 emg 现场计算（见第 7 节） |
| **`fp` 列数随 trial 变（19/46/64）、`markers` 列数随 subject 变（76~202）** | **不是 bug**，是源 table 本来就这样（已用 MATLAB 核对）；读的时候按表头动态取列，别写死列数（见 5.4） |
| 输出体积 ~104 GB | 需要留足磁盘（E: 当时剩 732 GB，够） |
| 控制台是 GBK 编码 | 脚本给 stdout/stderr 加了 `errors="replace"`，不会因为文件名里的怪字符崩掉 |

---

## 9. 使用方法

### 9.1 依赖

- Python 3.13，用 **uv** 管理：`uv add scipy`（唯一新增依赖，用于读中间态 `.mat`）。
- 本机 MATLAB R2024a（`C:\Program Files\MATLAB\R2024a`），license 有效。

### 9.2 脚本

| 文件 | 作用 |
|---|---|
| `convert_mat_to_csv.py` | **唯一入口**：遍历 → 调 MATLAB 解码 → 读中间态 → 写 CSV → 算包络 → 校验 → 出报告 |
| `matlab/decode_subject.m` | MATLAB 侧解码函数：`.mat`(table) → 中间态 `.mat`（纯 double + 字符串 cell + 列名） |

中间态格式（MATLAB `save -v7`，scipy 能直接读）：

```
cols     : 1xM cell of char   原始列名（顺序不变）
num_data : NxM double        数值列；字符串列位置为 NaN
str_data : NxK cell of char  字符串列（K=0 表示没有）
is_str   : 1xM logical       标记每列是不是字符串
```

### 9.3 CLI

```bash
uv run python convert_mat_to_csv.py                     # 全量（21 个 subject）
uv run python convert_mat_to_csv.py --subject AB06      # 只转某个 subject（可重复）
uv run python convert_mat_to_csv.py --sensors emg imu   # 限定传感器
uv run python convert_mat_to_csv.py --conditions ramp   # 限定运动条件
uv run python convert_mat_to_csv.py --dry-run           # 只列清单不转换
uv run python convert_mat_to_csv.py --verify            # 只校验已有产物
uv run python convert_mat_to_csv.py --force             # 覆盖已存在的 CSV
uv run python convert_mat_to_csv.py --no-envelope       # 不算 emg_envelope
uv run python convert_mat_to_csv.py --matlab "D:\...\matlab.exe"
```

- **断点续跑**：目标 CSV 已存在就跳过（除非 `--force`），中途 Ctrl-C 不会丢进度。
- **报告**：每个 subject 一份 JSON，写在 `data_GeorgiaTech_csv/_conversion_report/<SUBJECT>.json`，
  含写入/跳过/失败数量和失败文件的详细原因。

### 9.4 实测性能

| 项 | 实测 |
|---|---|
| 单个 subject（1836 文件） | 249–273 s（约 4.3 min） |
| 单文件 | ≈ 0.28 s（MATLAB load + 解码 + 写中间态 + Python 读 + 写 CSV） |
| 全量 35,988 文件 + 2,999 个包络 | ≈ 1 h 40 min（21 个 subject 串行） |
| 输出体积 | **104.5 GB**（38,987 个 CSV） |

---

## 10. 验证方法与实测结果

### 10.1 已做的验证

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | 与根目录参考镜像逐字节一致 | 对 AB06/levelground 的 emg/fp/gcLeft/gcRight/gon/id/ik/jp/markers 做二进制 diff | **244 个文件全部逐字节一致，0 个不一致** ✅ |
| 2 | 与数据集自带 `_csv` 数值一致 | `assert_allclose` | emg 最大绝对差 5.1e-15，imu 5.0e-14（即 MATLAB 15 位文本截断量级）✅ |
| 3 | 列名与顺序 | 与 README 记录 / 数据集 `_csv` 对比 | 完全一致，`Header` 均在第 0 列 ✅ |
| 4 | 形状 | 每个文件行数/列数与源 table 对比（脚本内建断言） | 全部一致，0 失败 ✅ |
| 5 | 覆盖完整度 | 源 `.mat` 清单 ↔ 产物清单逐个比对 | 35,988 个源文件 → **缺失 0、多余 0** ✅ |
| 6 | 表头规范 | 38,987 个产物逐个检查首列名 | 首列全部是 `Header`；列数与源 table 一致（`fp`/`markers` 变列数已单独核对）✅ |
| 7 | 包络吻合度 | 与 136 个参考包络逐通道算 corr | 平均 0.9954，277/297 通道 ≥ 0.99 ✅ |
| 8 | 随机抽样数值 | 21 个 subject 随机抽 60 个文件 vs 数据集自带 `_csv` | 异常 0，最大绝对差 5.0e-13 ✅ |
| 9 | 全量跑通 | 21 个 subject 跑完，报告里 0 失败 | 写入 35,628 + 跳过 360（先前已转的 AB06）+ **失败 0** ✅ |
| 10 | 临时文件清理 | 每个 subject 处理完删中间态目录 | `_work/` 残留为空 ✅ |

### 10.2 怎么自己复验

```bash
# 结构与数量
uv run python convert_mat_to_csv.py --verify --subject AB06

# 逐字节对比参考镜像（示例）
cmp levelground/emg/levelground_ccw_fast_01_01.csv \
    data_GeorgiaTech_csv/AB06/levelground/emg/levelground_ccw_fast_01_01.csv && echo IDENTICAL

# 随便读一个
python -c "import pandas as pd; d=pd.read_csv('data_GeorgiaTech_csv/AB06/levelground/imu/levelground_ccw_fast_01_01.csv'); print(d.shape, list(d.columns[:4]))"
```

---

## 11. 下一阶段：改造 `view_*` 脚本（✅ 已完成，见 [`view_scripts_adapt.md`](view_scripts_adapt.md)）

现在数据齐了，但脚本还是只认「`<repo>/<condition>/<modality>/`」这一层布局。
下一步要做：
1. **`DATA_DIR` 参数化**
   现在是 `Path(__file__).parent`，要改成「先找 `data_GeorgiaTech_csv/`，找不到再退回旧的根目录布局」，
   并支持命令行 `--data-dir` / 环境变量覆盖。

2. **加 subject 选择器**
   界面顶部多一个受试者下拉框（AB06 … AB28），和现有的「运动条件」下拉框同级。
   切换 subject 时要重建 trial 列表（现在 `_load_trials()` 只扫 `<DATA_DIR>/<cond>/emg/`，
   要改成 `<DATA_DIR>/<subject>/<cond>/emg/`）。

3. **trial 列表的配对逻辑保持不变**
   `view_emg_imu.py:208-211` 是「`emg/` 与 `imu/` 同名文件的交集」，
   换目录后逻辑照搬即可 —— 现在 21 个 subject 的 emg/imu 齐全，交集就是全集。

4. **`emg_envelope` 联动**
   `view_emg_envelope.py:193` 读 `<cond>/emg_envelope/<trial>.csv`，
   加上 subject 层即可；注意新目录里 `emg_envelope` 是在 `emg` 的**同级**（不是 `emg/` 里面）。

5. **旧根目录那套怎么办**
   根目录的 `levelground/ ramp/ stair/ treadmill/` 只是 AB06 的旧镜像且有缺失，
   可以直接用新产物替换掉，或者保留作为「单 subject 快速查看」的入口。
   建议**保留**，并在 `DATA_DIR` 探测里优先用 `data_GeorgiaTech_csv/`。

6. **顺带可以做的**
   - `view_emg_imu.py` 目前每个通道一条曲线，11 通道 + IMU 叠加很挤；可以加「归一化/双 Y 轴」选项。
   - `view_emg.py` 没有像 `view_emg_imu.py` 那样把时间轴归零（`t = df["Header"].values`），
     统一一下更好比较。
   - `view_emg_envelope.py` 的树形勾选联动已经有；可以补一个「原始 vs 包络」差值视图。

> **实施结果**：1~5 全部完成，做法见 [`view_scripts_adapt.md`](view_scripts_adapt.md)。
> 第 6 项按「行为完全不变」的要求**刻意没做**，留在那里作为以后的可选项。
> 路径解析抽到了新模块 `gt_dataset.py`（新布局 / 旧布局自动探测 + `--data-dir` / 环境变量覆盖）。

> **后续新增功能**：步态周期可视化（把 `gcRight` 接到 `view_emg_imu.py`），
> 见 [`gait_cycle_view.md`](gait_cycle_view.md)。
