# Real-time Fingertip Tracker

一个循序渐进的计算机视觉练习项目：从读取摄像头开始，逐步实现手部关键点检测、食指轨迹追踪、轨迹平滑和空中绘图。

## 初学者阅读说明

项目中由我们编写的 Python、C++ 和配置文件均带有详细中文注释。建议先阅读注释，再单步运行程序，按以下顺序学习：

1. `src/step1_camera.py`：摄像头、循环、FPS 和 OpenCV 基本绘图。
2. `src/step2_hand_landmarks.py`：MediaPipe 模型输入、视频时间戳和 21 个关键点。
3. `src/step3_fingertip_trail.py`：数据结构、左右手标签、轨迹历史和追踪中断。
4. `src/step4_fingertip_pan_tilt.py`：指尖平滑、坐标映射、串口协议和云台方向校准。
5. `firmware/i2c_scanner/src/main.cpp`：ESP32、I²C 和串口调试。
6. `firmware/servo_center_test/src/main.cpp`：PCA9685寄存器、PWM和双舵机安全测试。
7. `firmware/serial_pan_tilt/src/main.cpp`：串口解析、安全限位、限速运动和超时回中。

以下内容不会人工添加注释，因为它们不是我们编写的源代码：

- `.venv/`：pip 安装的第三方 Python 库。
- `.pio/`：PlatformIO 自动生成的编译产物。
- `__pycache__/` 和 `*.pyc`：Python 自动生成的字节码缓存。
- `models/*.task`：MediaPipe 二进制模型文件，不能作为文本编辑。
- 固件目录中由 PlatformIO 自动生成的 `.vscode` 文件。

## 当前进度

- [x] Step 1：摄像头画面与实时 FPS
- [x] Step 2：检测并绘制 21 个手部关键点
- [x] Step 3：追踪食指指尖并绘制轨迹
- [x] Step 4：平滑指尖坐标并通过串口控制二自由度云台
- [x] Hardware 1：ESP32-S3 与 PCA9685 I²C 扫描
- [x] Hardware 2：双舵机中位、方向、范围和速度测试
- [x] Hardware 3：带安全限位和超时保护的串口控制固件

## Step 1

推荐使用 VS Code 打开整个项目文件夹，而不是只打开单个 Python 文件：

```powershell
code .
```

首次打开时安装 VS Code 推荐的 Python 扩展。项目已经配置为使用 `.venv` 中的解释器。打开 `src/step1_camera.py` 后，可按 `F5` 并选择 `Step 1: Camera and FPS` 启动调试。

也可以在 VS Code 集成终端中运行：

在 PowerShell 中进入项目目录并运行：

```powershell
.\.venv\Scripts\python.exe .\src\step1_camera.py
```

按 `Q` 或 `Esc` 退出。如果默认摄像头无法打开，可尝试：

```powershell
.\.venv\Scripts\python.exe .\src\step1_camera.py --camera 1
```

## Step 2

在 VS Code 中按 `F5`，选择 `Step 2: Hand Landmarks`。程序会绘制每只手的 21 个关键点，食指指尖（第 8 号关键点）显示为红色。

也可以在集成终端中运行：

```powershell
.\.venv\Scripts\python.exe .\src\step2_hand_landmarks.py
```

## Step 3

在 VS Code 中按 `F5`，选择 `Step 3: Fingertip Trail`。左右手分别保存最近 120 帧的食指指尖坐标并绘制轨迹。

- `C`：清除左右手轨迹
- `Q` 或 `Esc`：退出

也可以修改轨迹长度：

```powershell
.\.venv\Scripts\python.exe .\src\step3_fingertip_trail.py --trail-length 240
```

## Step 4

先通过PlatformIO打开并上传`firmware/serial_pan_tilt`。上传时关闭舵机外部5V；确认固件串口输出`READY_FOR_FINGERTIP_COMMANDS`后关闭串口监视器，因为Python需要独占同一个COM端口。

首次联调强烈建议不安装激光笔，并先运行只做视觉和映射的预览模式：

```powershell
.\.venv\Scripts\python.exe .\src\step4_fingertip_pan_tilt.py --dry-run
```

预览正常后，接通舵机外部5V并运行实际控制：

```powershell
.\.venv\Scripts\python.exe .\src\step4_fingertip_pan_tilt.py --port COM7 --hand Right
```

如果ESP32不是COM7，请在Windows设备管理器或PlatformIO上传输出中查看真实端口，也可以保留`--port auto`让程序自动选择。

- `H`：反转CH0水平映射方向。
- `V`：反转CH1俯仰映射方向。
- `P`：暂停/恢复跟踪；暂停时云台回中。
- `C`：手动回中。
- `Q`或`Esc`：回中并退出。

默认只用右手控制；可以将`--hand Right`改成`Left`或`Any`。手指短暂遮挡时先保持最后位置，持续丢失后自动回中；ESP32超过700毫秒收不到电脑命令也会独立触发回中保护。
