# AB06 数据集 CSV 格式说明

> 受试者: AB06 | 采集日期: 2018年10月9日 | 格式: CSV (由 MATLAB .mat 转换而来)

## 目录结构

```
10_09_18/
├── levelground/          # 平地行走 (30 trials)
│   ├── conditions/       # 试验条件标记
│   ├── emg/              # 表面肌电信号
│   ├── fp/               # 力板/跑步机力数据
│   ├── gcLeft/           # 左脚步态周期事件
│   ├── gcRight/          # 右脚步态周期事件
│   ├── gon/              # 量角器关节角度
│   ├── id/               # 逆动力学 (关节力矩/力)
│   ├── ik/               # 逆运动学 (关节角度)
│   ├── imu/              # IMU 加速度+陀螺仪
│   ├── jp/               # 关节功率
│   └── markers/          # 光学标记点三维坐标
├── ramp/                 # 坡道行走 (63 trials)
│   └── (同上 11 种数据类型)
├── stair/                # 楼梯行走 (42 trials)
│   └── (同上 11 种数据类型)
└── treadmill/            # 跑步机行走 (8 trials)
    └── (同上 11 种数据类型)
```

**共计 4 种运动条件 × 11 种数据类型 = 143 个子文件夹，1573 个 CSV 文件**

## 运动条件与文件命名

### 1. levelground (平地行走, 30 trials)

命名格式: `levelground_{方向}_{速度}_{trial}_{segment}.csv`

| 字段 | 含义 | 取值 |
|------|------|------|
| 方向 | 行走方向 | `cw` (顺时针), `ccw` (逆时针) |
| 速度 | 行走速度 | `fast`, `normal`, `slow` |
| trial | 试验编号 | 01~05 |
| segment | 数据段 | 01 |

示例: `levelground_ccw_fast_01_01.csv` = 逆时针快速行走第1次试验第1段

### 2. ramp (坡道行走, 63 trials)

命名格式: `ramp_{坡度编号}_{方向}_{trial}_{segment}.csv`

| 字段 | 含义 | 取值 |
|------|------|------|
| 坡度编号 | 坡度等级 | 1~6 (不同倾斜角度) |
| 方向 | 行走方向 | `l` (左/上坡), `r` (右/下坡) |
| trial | 试验编号 | 01~05 |
| segment | 数据段 | 01~06 |

示例: `ramp_3_l_01_02.csv` = 3号坡度上坡第1次试验第2段

### 3. stair (楼梯行走, 42 trials)

命名格式: `stair_{楼梯编号}_{方向}_{trial}_{segment}.csv`

| 字段 | 含义 | 取值 |
|------|------|------|
| 楼梯编号 | 楼梯类型 | 1~4 |
| 方向 | 行走方向 | `l` (上楼), `r` (下楼) |
| trial | 试验编号 | 01~02 |
| segment | 数据段 | 01~06 |

示例: `stair_2_r_01_03.csv` = 2号楼梯下楼第1次试验第3段

### 4. treadmill (跑步机行走, 8 trials)

命名格式: `treadmill_{trial}_{segment}.csv`

| 字段 | 含义 | 取值 |
|------|------|------|
| trial | 试验编号 | 01~08 |
| segment | 数据段 | 01 |

示例: `treadmill_03_01.csv` = 跑步机第3次试验第1段

## 数据类型详细说明

所有 CSV 文件第一列均为 `Header` (时间戳, 单位: 秒)。
不同数据类型的采样频率不同，因此行数不同。

### conditions (试验条件)
- **列数**: 1 (仅 `stand` 列)
- **行数**: 3068
- **说明**: 记录试验中的站立/运动条件标记

### emg (表面肌电信号)
- **列数**: 12 (Header + 11 个肌肉通道)
- **行数**: 15336 (采样率较高)
- **列名**:

| 列名 | 肌肉 |
|------|------|
| Header | 时间戳 (秒) |
| gastrocmed | 腓肠肌内侧头 |
| tibialisanterior | 胫骨前肌 |
| soleus | 比目鱼肌 |
| vastusmedialis | 股内侧肌 |
| vastuslateralis | 股外侧肌 |
| rectusfemoris | 股直肌 |
| bicepsfemoris | 股二头肌 |
| semitendinosus | 半腱肌 |
| gracilis | 股薄肌 |
| gluteusmedius | 臀中肌 |
| rightexternaloblique | 右腹外斜肌 |

### fp (力板/跑步机力数据)
- **列数**: 64 (Header + 63 个力通道)
- **行数**: 15336
- **说明**: 包含跑步机左右侧、4个力板 (FP1/FP2/FP5/FP6) 及合力 的力 (v) 和力矩 (moment) 的 xyz 分量
- **列名格式**: `{设备}_{量}_{轴}`
  - 设备: `Treadmill_R`, `Treadmill_L`, `FP1`, `FP2`, `FP5`, `FP6`, `Combined`
  - 量: `vx/vy/vz` (力), `px/py/pz` (位置), `moment_x/moment_y/moment_z` (力矩)

### gcLeft / gcRight (步态周期事件)
- **列数**: 3 (Header, HeelStrike, ToeOff)
- **行数**: 3068
- **说明**: 记录每一步的脚跟着地 (HeelStrike) 和脚趾离地 (ToeOff) 事件时间

### gon (量角器关节角度)
- **列数**: 6
- **行数**: 15336
- **列名**: `Header`, `ankle_sagittal`, `ankle_frontal`, `knee_sagittal`, `hip_sagittal`, `hip_frontal`
- **说明**: 量角器测量的踝关节、膝关节、髋关节角度 (矢状面/额状面)

### id (逆动力学 - 关节力矩/力)
- **列数**: 24
- **行数**: 3068
- **说明**: 通过逆动力学计算得到的各关节力矩和骨盆力
- **列名格式**: `{关节}_{自由度}_moment` 或 `{关节}_force`
  - 关节: pelvis, hip, lumbar, knee, ankle, subtalar, mtp
  - 后缀: `_moment` (力矩), `_force` (力)
  - 侧别: `_r` (右), `_l` (左)

### ik (逆运动学 - 关节角度)
- **列数**: 24
- **行数**: 3068
- **说明**: 通过逆运动学计算得到的各关节角度
- **列名格式**: `{关节}_{自由度}` 或 `{关节}_{自由度}_{侧别}`
  - 包括: pelvis (tilt/list/rotation/tx/ty/tz), hip (flexion/adduction/rotation), knee_angle, ankle_angle, subtalar_angle, mtp_angle, lumbar (extension/bending/rotation)

### imu (IMU 加速度+陀螺仪)
- **列数**: 25 (Header + 24 个传感器通道)
- **行数**: 3068
- **说明**: 4 个身体部位 (foot/shank/thigh/trunk) 的三轴加速度和三轴陀螺仪
- **列名格式**: `{部位}_{传感器}_{轴}`
  - 部位: `foot` (足), `shank` (小腿), `thigh` (大腿), `trunk` (躯干)
  - 传感器: `Accel` (加速度), `Gyro` (陀螺仪)
  - 轴: `X`, `Y`, `Z`

### jp (关节功率)
- **列数**: 24
- **行数**: 3068
- **说明**: 各关节的功率 (力矩 × 角速度)
- **列名格式**: `{关节}_{自由度}_power`
  - 与 id (逆动力学) 的关节列表一致，后缀改为 `_power`

### markers (光学标记点三维坐标)
- **列数**: 85 (Header + 84 个坐标通道, 即 28 个标记点 × 3 轴)
- **行数**: 3068
- **说明**: 光学动作捕捉系统记录的标记点三维空间坐标
- **列名格式**: `{标记点}_{轴}` (轴: x/y/z)
- **标记点列表**:

| 部位 | 标记点 |
|------|--------|
| 骨盆 | L_ASIS, R_ASIS, L_PSIS, R_PSIS |
| 右大腿 | R_Thigh_Upper, R_Thigh_Front, R_Thigh_Rear, R_Knee_Lat |
| 左大腿 | L_Thigh_Upper, L_Thigh_Front, L_Thigh_Rear, L_Knee_Lat |
| 左小腿 | L_Shank_Upper, L_Shank_Front, L_Shank_Rear, L_Ankle_Lat |
| 右小腿 | R_Shank_Upper, R_Shank_Front, R_Shank_Rear, R_Ankle_Lat |
| 右足 | R_Heel, R_Toe_Tip, R_Toe_Med, R_Toe_Lat |
| 左足 | L_Heel, L_Toe_Tip, L_Toe_Med, L_Toe_Lat |

## 数据使用示例

### Python (pandas)

```python
import pandas as pd

# 读取 EMG 数据
emg = pd.read_csv('levelground/emg/levelground_ccw_fast_01_01.csv')
print(emg.columns)  # ['Header', 'gastrocmed', 'tibialisanterior', ...]
print(emg.shape)    # (15336, 12)

# 读取 IMU 数据
imu = pd.read_csv('levelground/imu/levelground_ccw_fast_01_01.csv')
print(imu.columns)  # ['Header', 'foot_Accel_X', 'foot_Accel_Y', ...]

# 读取逆运动学数据
ik = pd.read_csv('levelground/ik/levelground_ccw_fast_01_01.csv')
print(ik.shape)     # (3068, 24)

# 同一 trial 的不同数据类型可通过文件名对应
# 例如: levelground_ccw_fast_01_01 在 emg/ik/imu/... 中都有同名文件
```

### 同一 trial 的数据对齐

同一试验 (如 `levelground_ccw_fast_01_01`) 的所有数据类型共享相同文件名，但采样率不同:
- **emg, fp, gon**: 15336 行 (高频原始信号)
- **ik, id, jp, imu, markers, gcLeft, gcRight, conditions**: 3068 行 (处理后的运动学/动力学结果)

使用时需注意时间戳 `Header` 列进行对齐。

## 数据来源

- 原始数据为 MATLAB .mat 格式 (MCOS table)
- 使用 `convert_mat_to_csv.py` 脚本转换为 CSV 格式
- 原始数据集来源: AB06 受试者的公开生物力学数据集
