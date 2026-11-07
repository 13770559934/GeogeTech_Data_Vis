# 步态周期 (gc) 可视化：把 `gcRight` 画到 EMG/IMU 界面上

> 本文记录「`gcLeft` / `gcRight` 这两个传感器到底存了什么」这件事的反推过程，
> 以及「怎么把它接到 `view_emg_imu.py` 的 EMG/IMU 界面上」的设计与验证方法。
>
> 前置文档：[`transfer_mat2csv.md`](transfer_mat2csv.md)（`.mat` → `.csv` 的转换说明）、
> [`view_scripts_adapt.md`](view_scripts_adapt.md)（view 脚本适配全量数据集）。
>
> 状态：**已完成**。

---

## 1. 为什么要加

### 1.1 改之前的处境

`view_emg_imu.py` 改完之后是「上图 EMG + 下图 IMU」两个面板，右腿的 11 块肌肉
和 4 个 IMU 部位都能看。但缺一个东西：**时间参照**。

分析 EMG/IMU 时经常要问「这个肌肉放电峰对应的是不是支撑相初期」、
「足部加速度的冲击峰落在步态周期的哪个位置」。没有事件标记就只能靠肉眼看波形，
既不准也费劲。

数据集里其实**有**这份数据 —— `gcLeft/` / `gcRight/` 两个传感器就是干这个的 ——
但改造完成之后仓库里**没有任何代码读过它**（全仓 grep `HeelStrike` / `ToeOff`
只命中 MD 文档里的目录名）。

### 1.2 改完之后

```
┌─ EMG (V) ──────────────┐   ← 叠加事件竖线 + 支撑相色带
├─ IMU ───────────────────┤   ← 叠加事件竖线 + 支撑相色带
└─ 步态周期 GC (右脚) ────┘   ← 新增面板: 数据原貌 (两条锯齿斜坡)
```

三个面板共享同一条时间轴：

| 图元 | 含义 | 样式 |
|---|---|---|
| 红色实线竖线 | 右脚**跟触**（heel strike） | 贯通全高，半透明 |
| 蓝色虚线竖线 | 右脚**离地**（toe off） | 贯通全高，半透明 |
| 琥珀色色带 | **支撑相**（跟触 → 离地） | 贴在面板底部 7%，视口锚定 |

侧栏多一组「步态周期 (gcRight)」开关：事件竖线 / 支撑相色带 / 仅显示步行段。

---

## 2. `gcRight` 到底是什么

### 2.1 三列与采样率

`gcLeft/` 和 `gcRight/` 各只有一个 CSV，3 列：

| 列 | 类型 | 说明 |
|---|---|---|
| `Header` | 数值 | 时间戳（秒），200 Hz |
| `HeelStrike` | 数值 | **不是时间戳**，见 2.2 |
| `ToeOff` | 数值 | **不是时间戳**，见 2.2 |

`gcRight` 的 `Header` 与同一 trial 的 `imu` 的 `Header` **逐位相同**
（400 个 trial 验证，0 处不符；行数、200 Hz 栅格、绝对时间戳全同），
所以步态事件可以直接贴到 IMU 面板上，**不需要重采样**。

### 2.2 锯齿斜坡

`HeelStrike` / `ToeOff` 是一条 **0→100 的锯齿相位斜坡**：
每个步态周期内线性爬升，事件发生的那一刻值瞬间从 100 跳回 0。

```
t=14.7900  HS=100.0000  TO= 33.2335    ← 上一周期末尾，斜坡到顶
t=14.7950  HS=  0.0000  TO= 33.5329    ← 跟触事件发生在此处，斜坡归零
t=14.8000  HS=  0.3135  TO= 33.8323    ← 重新开始上升
t=14.8050  HS=  0.6270  TO= 34.1317
```

精确的构造规则（25917 个片段**全部**吻合）：

- 每一段恰好等于 `linspace(0, 100, n)`，其中 `n` = 该周期的**采样点数**
- 爬升是沿**行索引**线性的，**不是**沿时间线性的
  （时间空间线性 0/25917 匹配 —— 两者只差一个端点约定）
- `v[s] == 0.0` 恰好在事件那一拍；`v[s_next-1] == 100.0` 恰好在下一事件的前一拍

所以斜坡的陡峭程度反映的是「这个周期有多少个采样点」，
而不是「这个周期有多长」—— 步频高的段斜率陡。

> ⚠️ **根目录 `README.md` 里原来写的「记录每一步的脚跟着地 (HeelStrike) 和脚趾离地
> (ToeOff) 事件时间」是不准确的**，已改正。

### 2.3 事件怎么提取

事件下标 = **斜坡「离开零点」的那一拍**：

```python
s = np.where((v[:-1] == 0.0) & (v[1:] > 0.0))[0]
事件时刻 = Header[s]
```

**不要用 `np.diff(v) < 0`**。原因有二（2999 个 trial 全量统计）：

| 差异 | trial 数 | 原因 |
|---|---|---|
| 规范规则比 `diff<0` **多** 1 个 | 184 | 文件开头有一段字面 0（人还没起步），`diff` 法漏掉每列的第一个事件 |
| 规范规则比 `diff<0` **少** 1 个 | 28 | 尾部「归零后不再上升」的截断，`diff` 法会多算一个，规范规则自动排除 |

前导零段长度（全量统计，单位：采样点）：

| min | p50 | p95 | max |
|---|---|---|---|
| 0 | 154 | 313 | 2629 |

最长 2629 拍 = 13.1 s —— 跑台起步前人还没开始走。

### 2.4 左腿右腿的关系

`gcLeft` 与 `gcRight` 的对应关系（MATLAB 直接读**原始 `.mat`** 确认，
不是转换后的 CSV）：

| 条件 | `HeelStrike` | `ToeOff` |
|---|---|---|
| levelground / ramp / stair | 左文件 = 左腿，右文件 = 右腿 | 同左，各是本腿 |
| **treadmill** | 左文件 = 左腿，右文件 = 右腿 | **两份逐字节相同（都是右脚）** |

即：**跑台拿不到左脚离地事件**（`gcLeft.ToeOff` 里装的是右脚的）。
地面条件下 `gcLeft.ToeOff` 与 `gcRight.ToeOff` 相差约半个周期（左右交替）。

本功能只用 `gcRight`，两种条件下都自洽。

### 2.5 HS 列表与 TO 列表不按下标对齐

trial 通常从**摆动相中途**开始，所以**第一个 TO 会出现在第一个 HS 之前**。
按下标 `zip(t_hs, t_to)` 会配出**负时长**的支撑相：

```
levelground_ccw_fast_01_01:
  按下标 zip      支撑相时长: [-0.66 -0.45 -0.45 -0.43]   ← 负的！
  按时间顺序配对  支撑相时长: [ 0.485 0.485 0.50 ...]
```

必须用 `np.searchsorted(t_to, t_hs, side="right")` 给每个 HS 配「其后最近的 TO」。

### 2.6 静止段会把斜坡拉长

levelground / ramp / stair 的第一个「周期」可以长达 **4.37 s**
（平地行走的 `levelground_ccw_fast_01_01` 实测 15.105 s → 19.475 s）。
原因不是 bug：那段 `conditions/Label` 是 `stand` / `idle` —— 人站着不动，
MATLAB 只是把斜坡线性拉过去了。

这会让基于斜坡的统计失真：全量 50444 段支撑相里，占周期比例的中位数是
**58.7%**（正常步态），但最小 1.2% 最大 99.0% —— 极值全部来自这些被拉长的段。
另有 1075 个跟触事件后面没有配对的离地（trial 在支撑相中途结束）。

### 2.7 `conditions` 传感器的可用性

「仅显示步行段」过滤要用 `conditions` 的 `Label` 列：

| 条件 | `conditions` 列 | 采样率 | `Header` 与 gcRight | 可用 |
|---|---|---|---|---|
| levelground / ramp / stair | `Header, Label` | 200 Hz | **逐位相同** | ✅ 标签可按行号一一对应 |
| **treadmill** | `Header, Speed` | 1000 Hz | **时间原点不同**（相对时间，从 0.0 起） | ❌ 没有 `Label` |

`Label` 的取值：

- 平地：`{idle, stand, stand-walk, walk, walk-stand, turn1, turn2}`
- 坡道：`{idle, rampascent, rampascent-walk, rampdescent, rampdescent-walk, walk-rampascent, walk-rampdescent}`

---

## 3. 实测到的事实

| # | 事实 | 实测结果 |
|---|---|---|
| 1 | `gcRight` 文件齐全度 | 2999 个文件，**列名异常 0 个**；每 subject 136~153 个 |
| 2 | 采样率 | 全部 200 Hz |
| 3 | 斜坡构造 | 25917/25917 段 == `linspace(0,100,n)`（索引空间） |
| 4 | 事件端点 | `v[s]==0.0`、`v[s_next-1]==100.0` 全部成立 |
| 5 | 事件数 / trial | HS：min 8 / 中位 12 / max 152；TO 同 |
| 6 | 前导零段 | min 0 / p50 154 / p95 313 / max 2629 拍 |
| 7 | 规范规则 vs `diff<0` | 多 1 个：184 trial；少 1 个：28 trial（见 2.3） |
| 8 | 支撑相占周期 | 中位 **58.7%**（快走 53% / 正常 56.6% / 慢走 58.5% —— 越慢占比越高，生理正确） |
| 9 | `gcRight.Header == imu.Header` | 400 trial 抽样，**0 处不符** |
| 10 | `imu.t0 − emg.t0` | levelground (646) 全 0；ramp (1330) 1 个 −0.001 s；stair (873) 全 0；**treadmill (150) 118 个非零，最大 +0.004 s** |
| 11 | `gcLeft.ToeOff` vs `gcRight.ToeOff` | 地面 = 不同；跑台 = 逐字节相同 |
| 12 | 事件时刻 vs 力板真值 | 跟触 **−6 ms**、离地 **−3.5 ms**（< 1 个 200 Hz 采样点 = 5 ms） |
| 13 | IMU 是哪条腿 | 足部加速度与右跟标记点加速度相关系数 0.26~0.46，与左跟仅 0.01~0.04 → **右腿** |

> 第 12 条的验证方法：跑台的竖直轴是 **`vy`**（`vz` 是前后向推力轴），
> 用 `Treadmill_R_vy` / `Treadmill_L_vz` 交叉验证，别用错轴。

---

## 4. 设计决策

### 4.1 为什么用 `gcRight` 而不是 `gcLeft`

IMU 与 EMG 都是右腿（事实 13），所以事件标记必须来自右腿。
`gcRight` 的 `HeelStrike` 是右脚跟触、`ToeOff` 在两种条件下都是右脚离地，
自洽。

### 4.2 为什么是三联图而不是把 GC 挤进 IMU 面板

GC 面板画的是**数据原貌**（两条锯齿斜坡），它的作用是让用户看懂
「事件竖线是从哪来的」—— 斜坡的垂直跳变就是事件本身。
另外两个面板上的竖线与色带是**应用层**，方便直接读信号。

### 4.3 为什么不把 `gcRight` 加进 `REQUIRED_SENSORS`

`REQUIRED_SENSORS` 决定 trial 列表的交集语义。加进去会出事：

- 旧布局根镜像里 `treadmill/gcRight` 只有 **7** 个文件，而 `treadmill/emg` 有 **8** 个
  → 交集会把 `treadmill_08_01` **静默**从 trial 列表里抹掉
- 后台 `.mat` → `.csv` 转换还在跑，`gcRight` 随时可能缺

所以走「**有就用、没有就降级**」的路径：缺 `gcRight` 时侧栏整组置灰、
标题变成「本 trial 无此文件」、状态栏显示「gcRight: 无」，但 trial 列表不受影响。

### 4.4 为什么要统一时间零点

改造前两个面板**各自归零**（`_plot` 用 EMG 的 `t0`，`_plot_imu` 用 IMU 的 `t0`），
而两个面板的 X 轴是联动的。事实 10 显示跑台 trial 上两者最多差 **4 ms**，
118/150 个跑台 trial 都存在这个错位。

改成全局唯一的 `self._t0`（取 EMG 的 `Header[0]`，因为 EMG 面板是 X 轴主控方）：

- 地面条件：**视觉零变化**（本来就相等）
- 跑台：消掉最多 4 ms 的错位

属于「顺手拔掉一个隐患」，不改变数据语义。

### 4.5 为什么不用 `np.diff(v) < 0`

见 2.3 —— 会漏掉每列的第一个事件（184 个 trial），
还会多算尾部截断（28 个 trial）。

### 4.6 为什么支撑相色带只排 `idle` / `stand`

`NON_WALK_LABELS = {"idle", "stand"}`。`turn1` / `turn2` / `rampascent` /
`stairdescent` 这些**也是真在走**（转圈、上下坡），排掉会丢真实的步态周期。

「仅显示步行段」**默认不勾** —— 默认显示忠实于原始数据，需要的人自己开。

### 4.7 为什么 EMG / IMU 两个面板都画

两个面板 X 轴联动、上下对照最直观。

代价是每个面板多 126 个图元（124 个支撑相色带 + 2 条竖线折线）。
实测（EMG 面板，11 条 EMG 曲线 × 15000 点，跑台 trial）：

| | 平移帧耗时 |
|---|---|
| 无叠加层 | 55.8 ms（18 fps） |
| 全开叠加层 | 62.3 ms（16 fps） |
| **增量** | **+6.6 ms（12%）** |

> ⚠️ **这里踩过一个坑**：最初用「一条 `InfiniteLine` 一个事件」的实现，
> 跑台一个 trial 就是 250 条竖线 + 124 个色带；三个面板 X 轴联动，
> 每帧要过 1000 多个图元，实测 **+42 ms（92%）**。
> 改成「一条 NaN 断开的折线画全部竖线」后每个面板只剩 2 个图元，
> 增量降到 +6.6 ms。详见 5.1。

### 4.8 为什么 `_plot_imu()` 不再 `clear()`

`clear()` 会把叠加层一起清掉，切 IMU 通道时就得重建几百个图元。
改成只 `removeItem(self._imu_curve)` —— 叠加层原地不动。

实测：切通道后叠加层图元的 `id` 完全不变、仍在场景里、X 范围不变。

---

## 5. `gt_gait.py` 的 API 契约

| 名字 | 签名 | 说明 |
|---|---|---|
| `GC_SENSOR` | `str` | `"gcRight"`（驼峰，大小写敏感 —— 其余传感器目录全小写） |
| `GaitEvents` | dataclass | `t_hs` / `t_to` / `t0`；`len()` 步数；`is_empty` |
| `find_event_indices` | `(ramp: np.ndarray) -> np.ndarray` | 从锯齿斜坡取事件下标 |
| `extract_events` | `(gc_df: pd.DataFrame, t0: float, keep=None) -> GaitEvents` | 提取事件；`keep` 是行级布尔掩码（做「仅步行段」过滤） |
| `pair_stances` | `(t_hs, t_to) -> tuple[np.ndarray, np.ndarray]` | 跟触 → 其后最近的离地 |
| `load_walk_mask` | `(cond_df, gc_header) -> np.ndarray \| None` | 行级「算不算在走」掩码；不可用时返回 `None` |
| `GaitOverlay` | 类 | `build(events)` / `attach(pw)` / `detach()` / `set_visible(ribbon, hs, to)` / `n_items` |

设计约束：**不 import 任何 Qt widget**（只用 `PySide6.QtCore` 的 `Qt.DashLine` 一个枚举），
所以解析部分可以不开 `QApplication` 直接跑、直接单测。

`GaitOverlay` 一个实例只服务一个面板 —— pyqtgraph 的图元只能存在于一个场景里，
所以 EMG 面板和 IMU 面板各要一个实例（事件数据共享）。

### 5.1 `GaitOverlay` 怎么画 250 条竖线

事件竖线**不用**一条 `InfiniteLine` 一个事件，而是拼成**一条 NaN 断开的折线**：

```
每个事件占 3 个点: (x, y_lo) → (x, y_hi) → NaN
```

NaN 把折线断开，pyqtgraph 就画成一根根独立竖线，但它们共用**一个**图元。
折线的 y 坐标取当前视口的 Y 范围（靠 ViewBox 的 `sigYRangeChanged` 更新），
所以竖线始终贯通整个面板高度。

三个面板 X 轴联动 → 一次平移要重画全部面板 → 图元数量直接乘 3。
这个优化把每面板的图元数从 374 降到 126，平移帧增量从 +42 ms 降到 +6.6 ms。

两个实现细节：

- 支撑相色带用 `LinearRegionItem` + `span=(0, 0.07)`。`span` 是**视口**比例，
  Y 轴自动缩放时色带位置不动。它内部自带 2 条边框 `InfiniteLine`，
  边框本来就是 `NoPen`（看不见）却仍会在每次视口变化时跑一遍
  `viewRangeChanged` —— 124 个色带就是 248 条多余的线，`setVisible(False)` 藏掉。
- `addItem(..., ignoreBounds=True)` 不能省：不加的话自动量程会把 X 轴撑到
  步态事件的时间跨度上，经 X 联动把别的面板视野带歪。

---

## 6. `view_emg_imu.py` 的改动清单

### 6.1 改前 / 改后

| 位置 | 改前 | 改后 |
|---|---|---|
| 模块顶部 import | `from gt_dataset import (...)` | 加 `from gt_gait import (...)` |
| `__init__` | `self._emg_curves`、`self._imu_df` | 加 `self._imu_curve`、`self._t0`、`self._gc_df`、`self._gc_events`、`self._gc_walk_mask`、`self._gc_overlay_emg`、`self._gc_overlay_imu` |
| `_build_ui` | 2 个面板，`setSizes([450, 450])` | 3 个面板（加 `plot_gc`），`setSizes([360, 300, 190])`，窗口 `1400×1000` |
| `_build_ui` 侧栏 | IMU 下拉框后直接 `addStretch()` | 中间插入 `_build_gc_box()` |
| `_on_trial_changed` | 读 emg / imu | 加读 gcRight（可选）+ conditions（可选），带 try/except 降级 |
| `_plot` | `self.plot_imu.clear()` | 先 `detach()` 两个叠加层**再** `clear()`；`self._t0` 统一；末尾加 `_rebuild_gait()` + `_plot_gc()` |
| `_plot_imu` | `pw.clear()` + `pw.autoRange()` | 只 `removeItem(self._imu_curve)`；`attach()` 叠加层；`t0` 用 `self._t0`；`autoRange()` → `_auto_range_y()` |
| 新增 | — | `_build_gc_box()` / `_apply_gc_visibility()` / `_rebuild_gait()` / `_update_gc_controls()` / `_gc_info_text()` / `_plot_gc()` / `_auto_range_y()` |

### 6.2 侧栏控件

```
步态周期 (gcRight):
  [红块] HS 跟触  [蓝块] TO 离地  [琥珀块] 支撑相
  [x] 事件竖线 (HS / TO)
  [x] 支撑相色带
  [ ] 仅显示步行段
```

- 「仅显示步行段」在**跑台**上置灰（`conditions` 没有 `Label` 列），tooltip 说明原因
- 没有 `gcRight` 时整个 group 置灰，标题改成「步态周期 (gcRight) — 本 trial 无此文件」

### 6.3 刻意**没有**改的部分

- **不把 `gcRight` 加进 `REQUIRED_SENSORS`** —— 见 4.3，会静默丢 trial
- **不做事件时刻补偿** —— 相对力板真值偏 −6 ms / −3.5 ms，已经小于 200 Hz 的 1 拍
  （5 ms），补偿只会引入假精度
- **不默认开启「仅显示步行段」** —— 默认显示忠实于原始数据
- **不给 `view_emg.py` / `view_emg_envelope.py` 加 GC** —— 本次只做 `view_emg_imu.py`；
  `gt_gait.py` 的解析层是纯函数，将来要复用直接 import
- **不显示左脚事件** —— 地面条件下 `gcLeft.ToeOff` 有，但 IMU/EMG 是右腿，
  显示左脚对当前分析没有意义

### 6.4 一处**有意的行为改变**

`_plot_imu()` 里的 `pw.autoRange()` 换成了 `_auto_range_y(pw, y)`（只重算 Y 轴）。

原因：pyqtgraph 0.14 的 `setXLink` 是**双向**的 —— 任一联动面板调 `autoRange()`
都会把 X 范围推给所有面板。改造前切 IMU 通道会把用户的缩放重置掉（现有行为），
三面板之后更糟。

> 0.14 **没有** `autoRange(axis="y")`（`ViewBox.autoRange` 的签名是
> `(padding, items, item)`），而 `setYRange(min, max)` 的 min/max 又是必填的，
> 所以只能自己算范围。

---

## 7. 边界情况与坑

| # | 情况 | 处理 |
|---|---|---|
| 1 | 没有 `gcRight` 文件 | 侧栏整组置灰 + 状态栏「gcRight: 无」，trial 列表不受影响 |
| 2 | `gcRight` 正被转换写入（半截文件） | `pd.read_csv` 包 try/except → 等价于「没有」 |
| 3 | trial 一个事件都没有 | `find_event_indices` 对长度 < 2 返回空数组；`is_empty` → 控件置灰 |
| 4 | 前导零段（最长 2629 拍） | 规范规则天然处理，无需特判 |
| 5 | 尾部「归零后不再上升」截断 | 规范规则自动排除（`diff<0` 会多算） |
| 6 | `gcLeft.ToeOff` 其实是右脚（跑台） | 只用 `gcRight`，不受影响；文档已说明 |
| 7 | 跑台 `conditions` 没有 `Label` | 「仅显示步行段」置灰 + tooltip |
| 8 | 旧镜像 `levelground/conditions` 是坏文件（单列 `stand`，无表头） | `pd.read_csv` 得到 `columns=['stand']` → 没有 `Label` → 置灰 |
| 9 | 叠加层被 `clear()` 误删 | `detach()` 必须在 `clear()` **之前**；`attach()` 幂等 |
| 10 | 叠加层把 X 轴撑开 | 一律 `addItem(..., ignoreBounds=True)` |
| 11 | 联动面板调 `autoRange()` 冲掉用户缩放 | GC 面板不调；`_plot_imu` 改成只重算 Y |
| 12 | Z 序被曲线盖住 | 色带 −30 / 竖线 −20 / 曲线 0 |
| 13 | 切 IMU 通道导致叠加层闪烁 | `_plot_imu` 只 `removeItem(curve)`，不 `clear()` |
| 14 | 支撑相配出负时长 | `pair_stances` 用 `searchsorted` 按时间配 |
| 15 | 静止段拉长出退化支撑相（占比 1.2%~99%） | 属数据原貌；「仅显示步行段」可滤掉 |

---

## 8. 怎么用

```bash
uv run python view_emg_imu.py --subject AB06 --condition treadmill --trial treadmill_01_01
uv run python view_emg_imu.py --data-dir .        # 旧的单 subject 根目录布局
```

侧栏「步态周期 (gcRight)」组：

- **事件竖线 (HS / TO)** —— 红色实线是右脚跟触，蓝色虚线是右脚离地
- **支撑相色带** —— 贴在面板底部的琥珀色带 = 支撑相
- **仅显示步行段** —— 滤掉 `idle` / `stand` 段里被拉长的伪事件（**跑台不可用**）

放大到 3~5 s 窗口就能看清：IMU 足部加速度的冲击峰与红色跟触线对齐。

---

## 9. 验证方法与实测结果

### 9.1 已做的验证

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 斜坡 == `linspace(0,100,n)`（索引空间） | ✅ 25917/25917 段 |
| 2 | 事件处 `v[s]==0.0`、前一拍 `v[s_next-1]==100.0` | ✅ 全部成立 |
| 3 | 规范规则比 `diff<0` 多找到第一个事件 | ✅ 184 个 trial 多 1 个 |
| 4 | `pair_stances` 后 `to − hs > 0` | ✅ 全通过（朴素 zip 会得到 −0.66 s） |
| 5 | 支撑相占周期比例 | ✅ 中位 58.7%，越慢占比越高 |
| 6 | `gcRight.Header == imu.Header` | ✅ 400 trial 抽样 0 处不符 |
| 7 | `gcRight` 覆盖 21 subject × 4 条件 | ✅ 2999 文件，列名异常 0 个 |
| 8 | 事件时刻 vs 力板真值 | ✅ 跟触 −6 ms / 离地 −3.5 ms |
| 9 | 加叠加层前后 X 轴范围 | ✅ 完全不变（`ignoreBounds=True` 生效） |
| 10 | 切 IMU 通道 | ✅ 叠加层图元 `id` 完全不变、仍在场景里、X 范围不变 |
| 11 | 显隐开关 | ✅ 只改可见性，不重建图元 |
| 12 | 换 trial | ✅ 叠加层图元数 == 色带数 + 2 条折线 |
| 13 | 旧布局缺 `gcRight`（`treadmill_08_01`） | ✅ 状态栏「gcRight: 无」，整组置灰，不崩 |
| 14 | 跑台「仅显示步行段」 | ✅ 置灰 |
| 15 | 旧布局坏 `conditions` 文件 | ✅ 置灰，不崩 |
| 16 | 「仅显示步行段」在平地 | ✅ 10 事件 → 9 事件 |
| 17 | 叠加层性能 | ✅ 平移帧 +6.6 ms（12%），见 4.7 |
| 18 | 竖线位置 | ✅ 红色跟触线与 IMU 足部加速度冲击峰对齐（目视） |

### 9.2 怎么自己复验

```bash
# 数据层（不需要 GUI）
uv run python -c "
import numpy as np, pandas as pd, os
from gt_gait import extract_events, pair_stances
base = os.path.join('data_GeorgiaTech_csv','AB06','treadmill')
df = pd.read_csv(os.path.join(base,'gcRight','treadmill_01_01.csv'))
ev = extract_events(df, df['Header'].values[0])
hs, to = pair_stances(ev.t_hs, ev.t_to)
assert len(ev) == 125, len(ev)
assert np.all(to > hs), '支撑相出现负时长!'
print('OK', len(ev), '步,', len(hs), '段支撑相')
"

# 离屏 GUI
QT_QPA_PLATFORM=offscreen uv run python -c "
import sys
from PySide6.QtWidgets import QApplication
from gt_dataset import Dataset
from view_emg_imu import EMGIMUViewer
app = QApplication(sys.argv[:1])
v = EMGIMUViewer(Dataset('data_GeorgiaTech_csv'), subject='AB06', condition='treadmill')
pi = v.plot_imu.getPlotItem()
before = v.plot_imu.getViewBox().viewRange()[0]
n = len(pi.items)
v.cmb_imu.setCurrentIndex(3)          # 切 IMU 通道
assert len(pi.items) == n, '叠加层被重建/丢失!'
assert v.plot_imu.getViewBox().viewRange()[0] == before, 'X 轴被撑开!'
print('OK', n, 'items;', v.statusBar().currentMessage())
"
```

### 9.3 人工过一遍界面

```bash
uv run python view_emg_imu.py --subject AB06 --condition treadmill --trial treadmill_01_01
```

1. 侧栏出现「步态周期 (gcRight)」组 + 三个色块图例；
2. EMG / IMU 面板底部出现琥珀色支撑相色带，全高有红实线(HS)、蓝虚线(TO)；
3. GC 面板出现两条锯齿斜坡 + 图例，纵向跳变处即事件；
4. 滚轮放大到 3~5 s 窗口 → 竖线变稀，IMU 曲线的冲击峰与红线对齐；
5. 切 IMU 通道 → 叠加层**不闪、不消失**，缩放**不再**被重置；
6. 切到 `--condition levelground` → 事件数从 125 变 10；
7. 切到 `--condition treadmill` → 「仅显示步行段」变灰；
8. 切 trial / subject / condition 都不崩。

---

## 10. 相关文件

| 文件 | 说明 |
|---|---|
| `gt_gait.py` | **新增** —— 步态事件解析 + pyqtgraph 叠加层 |
| `view_emg_imu.py` | 改 —— 三联图 + 步态叠加层 + 统一时间零点 |
| `README.md` | 改 —— 修正 `gcLeft` / `gcRight` 的错误描述 |
| `gt_dataset.py` | **未改动** —— `REQUIRED_SENSORS` 保持 `("emg", "imu")` |
| `project_struc/gait_cycle_view.md` | 本文件 |
| `project_struc/transfer_mat2csv.md` | 转换说明（前置文档） |
| `project_struc/view_scripts_adapt.md` | view 脚本改造（前置文档） |
