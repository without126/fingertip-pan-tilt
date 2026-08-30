"""步骤3：追踪左右手食指指尖，并绘制每只手最近一段时间的运动轨迹。"""

# 延迟求值类型注解，提升不同Python版本间的兼容性。
from __future__ import annotations

# argparse 用于读取命令行参数。
import argparse
# time 用于视频时间戳和FPS计算。
import time
# deque 是双端队列；设置maxlen后可以自动丢弃最旧的轨迹点。
from collections import deque
# Path 用于安全地拼接模型文件路径。
from pathlib import Path
# TypeAlias 用来声明更容易理解的类型别名。
from typing import TypeAlias

# OpenCV负责摄像头、颜色转换和轨迹绘制。
import cv2
# MediaPipe主体包，用于构造模型输入图像。
import mediapipe as mp
# MediaPipe Tasks基础接口。
from mediapipe.tasks import python
# MediaPipe视觉任务接口，包含HandLandmarker。
from mediapipe.tasks.python import vision


# 当前脚本在“项目/src”目录，向上两级得到项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 默认手部关键点模型的完整路径。
DEFAULT_MODEL = PROJECT_ROOT / "models" / "hand_landmarker.task"
# Point表示一个由整数x、y组成的像素坐标。
Point: TypeAlias = tuple[int, int]
# None表示追踪中断；它会让绘制出来的轨迹在此处断开。
TrailPoint: TypeAlias = Point | None

# OpenCV颜色顺序为BGR；为左右手和未知手设置不同的轨迹颜色。
TRAIL_COLORS = {
    "Left": (255, 120, 30),  # 左手：偏蓝橙色。
    "Right": (30, 200, 255),  # 右手：黄色。
    "Hand": (180, 80, 255),  # 无法判断左右手时：紫色。
}


def parse_args() -> argparse.Namespace:
    """定义并解析摄像头、分辨率、轨迹长度和模型路径参数。"""

    # 创建参数解析器。
    parser = argparse.ArgumentParser(description="Track index fingertip trajectories.")
    # 摄像头编号默认为0。
    parser.add_argument("--camera", type=int, default=0, help="Camera device index")
    # 请求画面宽度为1280像素。
    parser.add_argument("--width", type=int, default=1280, help="Requested frame width")
    # 请求画面高度为720像素。
    parser.add_argument("--height", type=int, default=720, help="Requested frame height")
    # --trail-length控制每只手最多保留多少帧的历史坐标。
    parser.add_argument(
        "--trail-length",
        type=int,
        default=120,
        help="Maximum number of recent frames kept for each hand",
    )
    # 允许用户替换默认MediaPipe模型。
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    # 解析命令行并返回结果。
    return parser.parse_args()


def to_pixel(landmark: object, width: int, height: int) -> Point:
    """把0~1之间的MediaPipe归一化坐标转换为图像像素坐标。"""

    # 读取x并限制在0~1，防止点落到图像范围之外。
    x = min(max(float(landmark.x), 0.0), 1.0)
    # 读取y并限制在0~1。
    y = min(max(float(landmark.y), 0.0), 1.0)
    # 缩放至图像宽高范围并转成整数坐标。
    return int(x * (width - 1)), int(y * (height - 1))


def hand_label(result, hand_index: int) -> str:
    """取得指定手的Left/Right标签；没有分类结果时返回Hand。"""

    # 先确认handedness中存在这一只手，并且该分类列表非空。
    if hand_index < len(result.handedness) and result.handedness[hand_index]:
        # 第0项是置信度最高的分类，category_name通常为Left或Right。
        return result.handedness[hand_index][0].category_name
    # 模型没有左右手信息时使用通用名称。
    return "Hand"


def draw_trail(frame, trail: deque[TrailPoint], color: tuple[int, int, int]) -> None:
    """连接相邻有效轨迹点；遇到None时留出断点，避免跨越追踪丢失区间。"""

    # deque支持快速追加，但索引不直观；转成list后方便逐项访问。
    points = list(trail)
    # 从第二个点开始，让每个点都能与前一个点组成线段。
    for index in range(1, len(points)):
        # 读取前一个历史点。
        previous_point = points[index - 1]
        # 读取当前历史点。
        current_point = points[index]
        # 任意一端为None都代表追踪曾中断，因此不画跨越中断的线。
        if previous_point is None or current_point is None:
            continue

        # index越大表示轨迹越新；新轨迹画得更粗，便于观察运动方向。
        thickness = 1 + int(4 * index / max(len(points) - 1, 1))
        # 在相邻两个有效点之间画抗锯齿线段。
        cv2.line(
            frame,  # 要绘制的图像。
            previous_point,  # 线段起点。
            current_point,  # 线段终点。
            color,  # 当前手对应的BGR颜色。
            thickness,  # 随时间变化的线宽。
            cv2.LINE_AA,  # 抗锯齿线型。
        )


def main() -> None:
    """初始化模型和摄像头，维护指尖历史队列并逐帧绘制轨迹。"""

    # 读取命令行参数。
    args = parse_args()
    # 至少需要两个点才能形成一条线段。
    if args.trail_length < 2:
        raise ValueError("--trail-length must be at least 2")
    # 模型不存在时提前报错，避免MediaPipe给出更难理解的底层错误。
    if not args.model.is_file():
        raise FileNotFoundError(f"Hand landmark model not found: {args.model}")

    # 使用DirectShow后端打开Windows摄像头。
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    # 请求指定画面宽度。
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    # 请求指定画面高度。
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    # 初始化失败时给出明确错误。
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}. Try --camera 1.")

    # 配置MediaPipe手部关键点模型。
    options = vision.HandLandmarkerOptions(
        # 指定本地模型文件。
        base_options=python.BaseOptions(model_asset_path=str(args.model)),
        # VIDEO模式会结合相邻帧进行追踪。
        running_mode=vision.RunningMode.VIDEO,
        # 最多识别两只手。
        num_hands=2,
        # 初次检测阈值。
        min_hand_detection_confidence=0.5,
        # 判断手仍在画面中的阈值。
        min_hand_presence_confidence=0.5,
        # 帧间追踪阈值。
        min_tracking_confidence=0.5,
    )

    # 为Left、Right、Hand分别创建固定最大长度的轨迹队列。
    trails: dict[str, deque[TrailPoint]] = {
        label: deque(maxlen=args.trail_length) for label in TRAIL_COLORS
    }
    # 记录启动时间，用来生成MediaPipe视频时间戳。
    start_time = time.perf_counter()
    # 记录FPS计时的上一帧时间。
    previous_time = start_time
    # MediaPipe要求时间戳严格递增，因此保存上一帧的毫秒值。
    previous_timestamp_ms = -1
    # 平滑FPS初始值。
    smoothed_fps = 0.0

    # 确保退出时释放摄像头和窗口资源。
    try:
        # 根据配置创建模型；with结束时自动释放模型资源。
        with vision.HandLandmarker.create_from_options(options) as landmarker:
            # 持续处理每一帧。
            while True:
                # 读取一帧图像。
                ok, frame = camera.read()
                # 读取失败时终止，避免处理无效frame。
                if not ok:
                    raise RuntimeError("The camera opened, but no frame could be read.")

                # 水平镜像画面。
                frame = cv2.flip(frame, 1)
                # 读取实际帧高和帧宽，用于坐标转换。
                height, width = frame.shape[:2]
                # 将OpenCV的BGR颜色顺序转换成MediaPipe需要的RGB。
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # 把NumPy图像包装为MediaPipe图像对象。
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

                # 计算从程序启动到当前帧的毫秒数。
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                # 保证本帧时间戳至少比上一帧大1毫秒。
                timestamp_ms = max(timestamp_ms, previous_timestamp_ms + 1)
                # 保存时间戳供下一帧使用。
                previous_timestamp_ms = timestamp_ms
                # 执行MediaPipe检测。
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                # 记录“本帧可见”的手标签，稍后据此判断哪条轨迹需要断开。
                visible_labels: set[str] = set()
                # 遍历本帧检测到的每一只手及其21个关键点。
                for hand_index, landmarks in enumerate(result.hand_landmarks):
                    # 获取Left、Right或Hand标签。
                    label = hand_label(result, hand_index)
                    # 标记这只手在本帧可见。
                    visible_labels.add(label)
                    # landmarks[8]固定代表食指指尖，将其转换为像素坐标。
                    fingertip = to_pixel(landmarks[8], width, height)
                    # 如果出现新的标签就新建队列，然后追加当前指尖坐标。
                    trails.setdefault(label, deque(maxlen=args.trail_length)).append(fingertip)

                    # 获取该手的显示颜色；未知标签回退到Hand颜色。
                    color = TRAIL_COLORS.get(label, TRAIL_COLORS["Hand"])
                    # 绘制实心彩色指尖圆点。
                    cv2.circle(frame, fingertip, 10, color, -1, cv2.LINE_AA)
                    # 在彩色点外绘制白色圆环，增强复杂背景下的可见性。
                    cv2.circle(frame, fingertip, 14, (255, 255, 255), 2, cv2.LINE_AA)
                    # 在指尖旁边绘制Left/Right标签。
                    cv2.putText(
                        frame,
                        label,
                        (fingertip[0] + 16, fingertip[1] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        color,
                        2,
                        cv2.LINE_AA,
                    )

                # 检查每一条历史轨迹是否在本帧失去对应的手。
                for label, trail in trails.items():
                    # 手刚消失时追加一个None；连续消失时不重复追加None。
                    if label not in visible_labels and (not trail or trail[-1] is not None):
                        trail.append(None)
                    # 在当前帧上绘制这条手部轨迹。
                    draw_trail(frame, trail, TRAIL_COLORS.get(label, TRAIL_COLORS["Hand"]))

                # 获取当前计时点。
                current_time = time.perf_counter()
                # 根据相邻帧间隔计算瞬时FPS。
                instantaneous_fps = 1.0 / max(current_time - previous_time, 1e-6)
                # 更新上一帧时间。
                previous_time = current_time
                # 指数移动平均让FPS读数更平稳。
                smoothed_fps = (
                    instantaneous_fps
                    if smoothed_fps == 0.0
                    else 0.9 * smoothed_fps + 0.1 * instantaneous_fps
                )

                # 显示FPS和检测到的手数。
                cv2.putText(
                    frame,
                    f"FPS: {smoothed_fps:.1f}  Hands: {len(result.hand_landmarks)}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                # 显示清空轨迹和退出的按键提示。
                cv2.putText(
                    frame,
                    "C: clear trails   Q/ESC: quit",
                    (20, 75),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                # 刷新实时结果窗口。
                cv2.imshow("Fingertip Tracker - Step 3", frame)

                # 读取键盘按键。
                key = cv2.waitKey(1) & 0xFF
                # Q或Esc退出程序。
                if key in (ord("q"), 27):
                    break
                # C键清空所有手的历史轨迹，但程序继续运行。
                if key == ord("c"):
                    # 分别清空每个deque。
                    for trail in trails.values():
                        trail.clear()
    finally:
        # 释放摄像头设备。
        camera.release()
        # 关闭所有OpenCV窗口。
        cv2.destroyAllWindows()


# 直接运行脚本时启动main，被其他模块导入时不自动启动。
if __name__ == "__main__":
    main()
