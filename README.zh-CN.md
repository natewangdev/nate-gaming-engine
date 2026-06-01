# Nate Gaming Engine (nge)

> 基于**硬件 HID** 的游戏自动化框架：用 ESP32-S3 模拟真实 USB 键鼠，通过 USB 串口接收指令；屏幕感知采用被动截屏。输入不经过任何被监控的软件 API，从客户端层面无法与真实键鼠区分。

English: 暂未提供（如需英文主文档可后续补充 `README.md`）。

---

## 目录

- [它是什么 / 为什么](#它是什么--为什么)
- [特性](#特性)
- [架构](#架构)
- [硬件与软件要求](#硬件与软件要求)
- [快速开始](#快速开始)
  - [1. 烧录固件](#1-烧录固件)
  - [2. 安装 Python 依赖](#2-安装-python-依赖)
  - [3. 运行示例](#3-运行示例)
- [串口协议](#串口协议)
- [拟人化参数](#拟人化参数)
- [Python API 速览](#python-api-速览)
- [项目结构](#项目结构)
- [示例脚本](#示例脚本)
- [风险与免责声明](#风险与免责声明)
- [许可证](#许可证)

---

## 它是什么 / 为什么

很多自动化方案（PyAutoGUI、pynput、`SendInput`、窗口消息注入等）都是**软件级**模拟，容易被反作弊的 hook 扫描和行为分析识别。`nge` 改用 **ESP32-S3 原生 USB HID**，让被控电脑看到的是一台**真实的 USB 键鼠设备**：

- 输入完全不经过 `SendInput` / 消息注入 / 全局 hook；
- 指令通道走 USB CDC 串口，与"输入注入"无关，不被当作可疑行为；
- 屏幕感知用桌面复制（DXGI）被动截屏，不读写游戏内存。

> 适合学习输入模拟 / 图像识别技术，或用于**无反作弊**的单机/离线场景。

## 特性

- ESP32-S3 自定义 **复合 HID**：绝对坐标鼠标（0–32767）+ 键盘。
- 简洁的**文本行串口协议**，每条命令带应答（`OK`/`PONG`/`ERR`）。
- **拟人化路径**：三次贝塞尔曲线、缓动、手抖、过冲回拉、随机停顿、**峰值速度上限**。
- 高层 Python API：`move_to` / `click`（支持落点散布 `spread`）/ `press` / `hotkey`。
- 像素坐标自动映射到 HID 设备坐标。
- 截屏（dxcam / mss）+ OpenCV 模板匹配。
- **OCR 文字识别**（RapidOCR，中文友好）：支持区域裁剪、只识别数字。
- **YOLO 目标检测**（ONNX + onnxruntime，运行时无需 PyTorch）：区域裁剪、坐标对齐屏幕。
- 决策循环引擎（`bot.py`）：优先级规则、冷却、拟人休息、会话上限。
- 图形测试工具（tkinter），含拟人参数**实时滑块**与 **OCR 框选识别**。
- 物理急停（开发板 BOOT 键）+ 软件急停（`STOP`）。

## 架构

```
[游戏PC屏幕] --截屏--> [视觉/决策 (Python)] --串口指令--> [ESP32-S3] --USB HID--> [游戏PC]
                                            (USB CDC)        (真实键鼠设备)
```

- **感知层（Python，运行在游戏 PC）**：截屏 + 图像识别。
- **传输层**：USB CDC 串口发送文本指令。
- **执行层（ESP32-S3 固件）**：把指令翻译成 HID 报文，通过 USB 注入游戏 PC。

拟人化逻辑全部在 Python 端生成（一连串绝对坐标点），固件只执行原子动作，保持简单稳定。

## 硬件与软件要求

**硬件**

- ESP32-S3 开发板（需**原生 USB OTG**，S3 自带）。
- USB 数据线，连接到开发板的**原生 USB 口**（非 UART 桥接口）。

**软件**

- [Arduino IDE](https://www.arduino.cc/en/software) + ESP32 开发板支持包（Espressif）。
- Python 3.10+（使用了 `X | Y` 类型注解语法）。
- 依赖见 [`requirements.txt`](requirements.txt)：`pyserial`、`numpy`、`opencv-python`、`dxcam`（Windows）、`mss`、`rapidocr-onnxruntime`（OCR）、`onnxruntime`（YOLO 检测）。

  > 仅测试键鼠时只需 `pyserial`；截屏/模板匹配需 `numpy`/`opencv-python`/`dxcam`/`mss`；文字识别额外需 `rapidocr-onnxruntime`；YOLO 检测额外需 `onnxruntime`（RapidOCR 已包含）。**训练/导出**模型才需 `ultralytics`+PyTorch，运行时不需要。

## 快速开始

### 1. 烧录固件

固件位于 [`firmware/nge_hid/nge_hid.ino`](firmware/nge_hid/nge_hid.ino)。

在 Arduino IDE 的 **Tools（工具）** 菜单设置：

| 选项 | 值 |
|------|-----|
| Board | `ESP32S3 Dev Module` |
| USB Mode | **`USB-OTG (TinyUSB)`** |
| USB CDC On Boot | **`Enabled`** |
| Port | 开发板对应的 COM 口 |

点击 **Upload** 上传。

> 上传后用串口工具发 `PING`，应回 `PONG`；发 `MA 16384 16384` 光标跳到屏幕中心；发 `CLK L` 左键点击。

### 2. 安装 Python 依赖

```powershell
# 完整依赖
pip install -r requirements.txt

# 或仅测试键鼠
pip install pyserial
```

### 3. 运行示例

> 运行前请**关闭串口监视器 / SSCOM**，COM 口同一时间只能被一个程序占用。

```powershell
cd D:\GitHub\nate-gaming-engine

# 图形测试工具（推荐，含拟人参数滑块）
python -m examples.gui_test

# 交互式命令行测试
python -m examples.test_io

# 最小冒烟示例
python -m examples.quickstart
```

## 串口协议

固件按行解析命令（以 `\n` 结尾），每条命令都有应答。坐标为 HID 设备坐标，范围 `[0, 32767]`。

| 命令 | 说明 | 应答 |
|------|------|------|
| `MA <x> <y>` | 绝对移动，x,y ∈ [0,32767] | `OK` |
| `BTN <L\|R\|M> <0\|1>` | 鼠标键 抬起(0)/按下(1) | `OK` |
| `CLK <L\|R\|M> [ms]` | 点击：按下→保持 ms（默认 20）→抬起 | `OK` |
| `WHEEL <delta>` | 滚轮，-127..127 | `OK` |
| `KD <code>` | 键按下（HID Usage 码，支持 `0x..`） | `OK` |
| `KU <code>` | 键抬起 | `OK` |
| `KP <code> [ms]` | 键点按（默认 20ms） | `OK` |
| `MOD <byte>` | 设置修饰键位掩码 | `OK` |
| `PING` | 连通性检测 | `PONG` |
| `STOP` | 释放全部键鼠 | `OK` |

像素 → 设备坐标换算：`x_dev = round(px * 32767 / (屏幕宽 - 1))`（Python 端 `Controller` 自动完成）。

## 拟人化参数

通过 `nge.humanize.HumanizeConfig` 配置，可在 GUI 滑块里实时调节：

| 参数 | 含义 | 默认 | 建议范围 |
|------|------|------|---------|
| `max_speed` | 最大移动速度（px/s），防瞬移核心 | 5500 | 4000–6000 |
| `curvature` | 路径弯曲度（占距离比例） | 0.18 | 0.10–0.25 |
| `jitter` | 每点随机抖动（px），模拟手抖 | 0.6 | 0.4–1.0 |
| `overshoot_chance` | 冲过目标再拉回的概率 | 0.25 | 0.15–0.35 |
| `pause_chance` | 途中插入迟疑停顿的概率 | 0.06 | 0.05–0.12 |
| `rate_hz` | 每秒路径点数（平滑度） | 144 | 60–250 |

```python
import nge
from nge.humanize import HumanizeConfig

ctrl = nge.connect(humanize=HumanizeConfig(max_speed=4500, pause_chance=0.10))
```

### 推荐起步组合

| 风格 | max_speed | curvature | jitter | overshoot | pause | rate |
|------|-----------|-----------|--------|-----------|-------|------|
| **拟人稳健（推荐）** | 5000 | 0.18 | 0.6 | 0.25 | 0.08 | 144 |
| 快速（手动操作感） | 9000 | 0.10 | 0.4 | 0.15 | 0.03 | 144 |
| 极度谨慎（慢而自然） | 3000 | 0.22 | 0.8 | 0.30 | 0.12 | 120 |

实际跑 bot 建议用"拟人稳健"档，瞬移风险最低。想直观对比参数效果，可在 GUI 里把 `max_speed` 拉到 2000、`pause_chance` 拉到 0.2，移动会明显变慢且有迟疑感。

```python
# 例：拟人稳健档
ctrl = nge.connect(humanize=HumanizeConfig(
    max_speed=5000, curvature=0.18, jitter=0.6,
    overshoot_chance=0.25, pause_chance=0.08, rate_hz=144,
))
```

## Python API 速览

```python
import nge

# 自动识别 ESP32-S3 端口并连接（内部会 PING 校验）
ctrl = nge.connect()                 # 或 nge.connect(port="COM7")

# 鼠标（坐标为像素，自动换算 + 拟人路径）
ctrl.move_to(960, 540)               # 拟人路径移动
ctrl.move_instant(100, 200)          # 瞬移（无拟人）
ctrl.click()                         # 左键点击
ctrl.click(button="R")               # 右键
ctrl.mouse_down("L"); ctrl.mouse_up("L")
ctrl.wheel(3)                        # 滚轮

# 键盘（键名见 nge/keymap.py）
ctrl.press("space")
ctrl.press("1")                      # 数字键
ctrl.hotkey("ctrl", "a")             # 组合键

# 点击落点散布：在目标半径 spread(px) 圆内随机落点，避免每次点同一像素
ctrl.click(960, 540, spread=10)

# 急停 / 关闭
ctrl.stop()
ctrl.transport.close()
```

屏幕尺寸默认取主显示器，可手动指定：`nge.connect(screen_size=(2560, 1440))`。

### OCR 文字识别

基于 RapidOCR（中文友好、CPU 快）。**务必传 `region` 只识别目标区域**以保证速度。

```python
from nge.capture import ScreenCapture
from nge.ocr import OCREngine

ocr = OCREngine()                    # 懒加载，首次调用才初始化模型
frame = ScreenCapture().grab()

region = (800, 0, 1760, 120)         # (left, top, right, bottom) 只识别顶部窄条
ocr.read_text(frame, region=region)              # -> 拼接成字符串
ocr.read(frame, region=region)                   # -> [OCRResult(text, score, x, y, box), ...]
ocr.find_text(frame, "确定", region=region)       # -> 最佳匹配（坐标可直接点击）
ocr.read_number(frame, region=region)            # -> 第一个数字（int/float），如读血量
ocr.read_numbers(frame, region=region)           # -> 区域内所有数字
```

### YOLO 目标检测

适合**会变形/变大小/位置不固定**的目标（怪物、掉落物、世界标记）；固定的 UI 图标用模板匹配更省事。运行时只用 `onnxruntime`，不背 PyTorch。

**准备模型**（仅训练/导出阶段需要 `ultralytics`）：

```powershell
pip install ultralytics
# 1) 标注数据集（Roboflow / X-AnyLabeling 等），训练自定义模型
yolo detect train data=d4.yaml model=yolo11n.pt epochs=100 imgsz=640
# 2) 导出 ONNX（之后运行时只需 onnxruntime）
yolo export model=runs/detect/train/weights/best.pt format=onnx
```

> COCO 预训练模型只认通用类别（人、车等），**识别游戏目标必须自己标注训练**。导出的 ONNX 会内嵌类别名，框架自动读取。

**使用**：

```python
from nge.capture import ScreenCapture
from nge.detect import YoloDetector

det = YoloDetector(r"C:\path\to\best.onnx")   # 懒加载；有 CUDA 自动用 GPU，否则 CPU
frame = ScreenCapture().grab()

# 整屏或区域检测（传 region 裁剪提速，和 OCR 一致）
dets = det.detect(frame, conf=0.4)            # -> [Detection(label, class_id, conf, x, y, box), ...]
dets = det.detect(frame, region=(0, 0, 1280, 720), conf=0.4, classes=["monster"])
best = det.find(frame, "monster", conf=0.45)  # -> 最高分的单个目标
# Detection 坐标为屏幕空间，可直接喂给 controller.click(best.x, best.y)
```

接进 bot：给 `Bot(..., yolo_model=...)`，规则里用 `ctx.detect(...)` / `ctx.find_object(...)`：

```python
bot = Bot(nge.connect(), ScreenCapture(),
          asset_root=ASSET_ROOT, yolo_model=r"C:\path\to\best.onnx")

@bot.rule(name="attack", priority=15, cooldown=0.3)
def attack(ctx):
    m = ctx.find_object("monster", conf=0.45)   # 坐标自动叠加截屏区域偏移
    if m:
        ctx.controller.click(m.x, m.y, spread=10)
        return True
    return False
```

### 决策循环（bot）

```python
import nge
from nge.bot import Bot, BotConfig
from nge.capture import ScreenCapture

bot = Bot(
    nge.connect(),
    ScreenCapture(),
    config=BotConfig(tick_hz=8, break_every=180),
    asset_root=r"C:\path\to\resources",   # 相对资源路径（模板等）的根目录
    yolo_model=r"C:\path\to\best.onnx",   # 可选：启用 ctx.detect / ctx.find_object
)

@bot.rule(name="pickup", priority=10, cooldown=0.4)
def pickup(ctx):
    m = ctx.find("templates/loot.png", threshold=0.85)   # 相对 asset_root 解析
    if m:
        ctx.controller.click(m.x, m.y, spread=8)
        return True                                       # True = 已行动
    return False

@bot.rule(name="potion", priority=30, cooldown=2.0)
def potion(ctx):
    if (hp := ctx.read_number(region=(1000, 1330, 1200, 1400))) and hp < 300:
        ctx.controller.press("q")
        return True
    return False

bot.run()   # Ctrl+C 停止；退出自动释放键鼠并关闭截图器
```

## 项目结构

```
nate-gaming-engine/
├─ firmware/
│  └─ nge_hid/nge_hid.ino   # ESP32-S3 固件：HID 复合设备 + 串口协议解析
├─ nge/
│  ├─ __init__.py           # 包入口，nge.connect()
│  ├─ logger.py             # 日志
│  ├─ keymap.py             # 键名 → HID Usage 码 + 修饰键位掩码
│  ├─ transport.py          # 串口封装（自动找口、发命令、等应答）
│  ├─ humanize.py           # 拟人路径：贝塞尔/缓动/抖动/过冲/限速/停顿
│  ├─ controller.py         # 高层 API：move_to / click / press / hotkey
│  ├─ capture.py            # dxcam / mss 截屏
│  ├─ vision.py             # OpenCV 模板匹配
│  ├─ ocr.py                # RapidOCR 文字识别（区域裁剪、数字识别）
│  ├─ detect.py             # YOLO 目标检测（ONNX + onnxruntime）
│  └─ bot.py                # 决策循环引擎：规则 / 冷却 / 拟人休息
├─ examples/
│  ├─ quickstart.py         # 最小冒烟示例
│  ├─ test_io.py            # 交互式命令行测试
│  ├─ gui_test.py           # 图形测试工具（拟人滑块 + OCR 框选）
│  ├─ ocr_example.py        # 区域 OCR 示例
│  ├─ detect_example.py     # YOLO 目标检测示例
│  └─ bot_example.py        # 决策循环示例骨架
├─ requirements.txt
├─ LICENSE
└─ README.zh-CN.md
```

## 示例脚本

| 脚本 | 用途 |
|------|------|
| `examples/gui_test.py` | tkinter 图形界面：坐标/点击/按键、拟人参数实时滑块、OCR 框选识别 |
| `examples/test_io.py` | 命令行交互菜单：逐项测试鼠标/键盘 |
| `examples/quickstart.py` | 连接→移动→点击→按键的最小流程 |
| `examples/ocr_example.py` | 区域 OCR：识别文字 / 数字 / 查找指定文字 |
| `examples/detect_example.py` | YOLO 目标检测：整屏/区域检测、按类查找 |
| `examples/bot_example.py` | 决策循环骨架：模板匹配 + OCR + 规则 |

## 风险与免责声明

- 硬件 HID 能规避**客户端**输入来源检测，但**无法规避服务端行为分析**（固定间隔、完美反应、超长在线、像素级一致走位等仍可能被标记）。
- 任何游戏自动化通常**违反游戏 EULA / 服务条款**，存在**封号风险**。
- 本项目仅供**学习输入模拟与图像识别技术**之用。是否在特定游戏中使用，以及由此产生的一切后果，由使用者自行承担。请遵守目标软件的服务条款与当地法律法规。

## 许可证

本项目基于 [MIT License](LICENSE) 授权。
