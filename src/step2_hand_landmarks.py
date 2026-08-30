"""步骤2：用MediaPipe检测每只手的21个关键点，并把骨架绘制到摄像头画面。"""

# 延迟求值类型注解，避免部分注解在程序启动时立即解析。
from __future__ import annotations

# argparse 负责解析 --camera、--width 等命令行参数。
import argparse
# time 负责生成视频时间戳以及计算FPS。
import time
# Path 提供跨平台的文件路径操作。
from pathlib import Path

# OpenCV负责摄像头、颜色转换、图形绘制和窗口显示。
import cv2
# MediaPipe主体包；后面会用 mp.Image 把OpenCV图像交给模型。
import mediapipe as mp
# MediaPipe Tasks的基础Python接口，用于指定模型文件。
from mediapipe.tasks import python
# MediaPipe Tasks的视觉接口，包含HandLandmarker等类。
from mediapipe.tasks.python import vision


# __file__是当前脚本路径；parents[1]向上两级得到项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 默认模型位于“项目根目录/models/hand_landmarker.task”。
DEFAULT_MODEL = PROJECT_ROOT / "models" / "hand_landmarker.task"

# MediaPipe为21个关键点规定了固定编号。
# 下列二元组表示需要连线的“起点编号、终点编号”，组合后形成手部骨架。
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),  # 手腕到拇指尖。
    (0, 5), (5, 6), (6, 7), (7, 8),  # 手腕到食指尖；8号点是食指尖。
    (5, 9), (9, 10), (10, 11), (11, 12),  # 掌部到中指尖。
    (9, 13), (13, 14), (14, 15), (15, 16),  # 掌部到无名指尖。
    (13, 17), (17, 18), (18, 19), (19, 20),  # 掌部到小指尖。
    (0, 17),  # 手腕连接到小指根部，使掌部轮廓闭合。
)


def parse_args() -> argparse.Namespace:
    """定义命令行参数并返回用户最终选择的参数值。"""

    # 创建参数解析器并设置帮助文字。
    parser = argparse.ArgumentParser(description="Draw MediaPipe hand landmarks.")
    # 默认使用0号摄像头；有多个摄像头时可以传入 --camera 1。
    parser.add_argument("--camera", type=int, default=0, help="Camera device index")
    # 请求画面宽度为1280像素。
    parser.add_argument("--width", type=int, default=1280, help="Requested frame width")
    # 请求画面高度为720像素。
    parser.add_argument("--height", type=int, default=720, help="Requested frame height")
    # 允许使用 --model 指定另一份模型；默认使用项目内的模型。
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    # 解析命令行并返回结果。
    return parser.parse_args()


def to_pixel(landmark: object, width: int, height: int) -> tuple[int, int]:
    """把MediaPipe的0~1归一化坐标转换成OpenCV图像中的整数像素坐标。"""

    # landmark.x通常在0~1之间；min/max把偶尔越界的数裁剪回合法范围。
    x = min(max(float(landmark.x), 0.0), 1.0)
    # 对纵坐标做同样的裁剪。
    y = min(max(float(landmark.y), 0.0), 1.0)
    # 图像最后一列是width-1，最后一行是height-1，因此按它们进行缩放。
    return int(x * (width - 1)), int(y * (height - 1))


def draw_result(frame, result) -> None:
    """把一次MediaPipe检测结果中的所有手部骨架绘制到frame上。"""

    # frame.shape依次为(高度, 宽度, 通道数)，这里只取前两项。
    height, width = frame.shape[:2]

    # result.hand_landmarks中每个元素代表一只手；enumerate同时给出手的序号。
    for hand_index, landmarks in enumerate(result.hand_landmarks):
        # 把这一只手的21个归一化关键点全部转换为像素坐标。
        points = [to_pixel(landmark, width, height) for landmark in landmarks]

        # 逐条绘制HAND_CONNECTIONS中定义的骨架连线。
        for start, end in HAND_CONNECTIONS:
            # points[start]和points[end]分别是线段两端的像素坐标。
            cv2.line(frame, points[start], points[end], (80, 220, 80), 2, cv2.LINE_AA)

        # 逐个绘制21个关键点，同时获得每个点的编号index。
        for index, point in enumerate(points):
            # 8号点是食指指尖：将它设为红色，其余点使用橙蓝色。
            color = (0, 0, 255) if index == 8 else (255, 180, 0)
            # 食指尖半径设为7像素，其余点设为4像素。
            radius = 7 if index == 8 else 4
            # thickness=-1表示画实心圆；颜色顺序是BGR而不是RGB。
            cv2.circle(frame, point, radius, color, -1, cv2.LINE_AA)

        # 如果模型没有给出左右手信息，先使用通用标签“Hand”。
        label = "Hand"
        # handedness可能为空，所以先检查索引存在以及该位置确实有结果。
        if hand_index < len(result.handedness) and result.handedness[hand_index]:
            # 取这一只手置信度最高的类别，通常为Left或Right。
            category = result.handedness[hand_index][0]
            # 将类别名称和0~1之间的置信度组合为显示文字。
            label = f"{category.category_name} {category.score:.2f}"

        # 取所有关键点中最靠左的x坐标作为标签横坐标。
        label_x = min(point[0] for point in points)
        # 标签放在手的最高点上方12像素，并保证y至少为30以免超出画面。
        label_y = max(30, min(point[1] for point in points) - 12)
        # 把左右手类别和置信度绘制到图像上。
        cv2.putText(
            frame,  # 被绘制的BGR图像。
            label,  # 例如“Left 0.98”。
            (label_x, label_y),  # 标签左下角位置。
            cv2.FONT_HERSHEY_SIMPLEX,  # OpenCV内置字体。
            0.7,  # 字号缩放。
            (0, 255, 255),  # BGR黄色。
            2,  # 文字线宽。
            cv2.LINE_AA,  # 抗锯齿。
        )


def main() -> None:
    """初始化摄像头和手部模型，然后逐帧执行检测与绘制。"""

    # 读取命令行参数。
    args = parse_args()
    # 在加载模型前检查文件是否存在，以便给出易懂的错误。
    if not args.model.is_file():
        raise FileNotFoundError(f"Hand landmark model not found: {args.model}")

    # 用Windows DirectShow后端打开指定摄像头。
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    # 请求摄像头宽度。
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    # 请求摄像头高度。
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    # 无法打开时停止程序并提示尝试另一个编号。
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}. Try --camera 1.")

    # 构造MediaPipe手部关键点检测器的配置对象。
    options = vision.HandLandmarkerOptions(
        # 告诉MediaPipe模型文件的绝对路径。
        base_options=python.BaseOptions(model_asset_path=str(args.model)),
        # VIDEO模式会利用前后帧信息进行追踪，比每帧独立检测更稳定。
        running_mode=vision.RunningMode.VIDEO,
        # 最多同时检测两只手。
        num_hands=2,
        # 初次检测一只手时，置信度至少为0.5。
        min_hand_detection_confidence=0.5,
        # 判断画面中仍存在这只手时，置信度至少为0.5。
        min_hand_presence_confidence=0.5,
        # 帧间追踪结果的置信度至少为0.5。
        min_tracking_confidence=0.5,
    )

    # 起始时间用于给VIDEO模式生成从0开始且持续递增的毫秒时间戳。
    start_time = time.perf_counter()
    # previous_time用于计算相邻帧间隔和FPS。
    previous_time = start_time
    # 保存上一帧传给MediaPipe的时间戳，初始值设为-1。
    previous_timestamp_ms = -1
    # 保存经过指数移动平均后的FPS。
    smoothed_fps = 0.0

    # 无论正常退出还是发生异常，finally都会释放摄像头。
    try:
        # with会创建检测器，并在离开代码块时自动释放其底层资源。
        with vision.HandLandmarker.create_from_options(options) as landmarker:
            # 持续处理摄像头帧，直到用户退出。
            while True:
                # 读取一帧BGR图像。
                ok, frame = camera.read()
                # 读取失败时停止，避免继续处理空图像。
                if not ok:
                    raise RuntimeError("The camera opened, but no frame could be read.")

                # 水平翻转，得到符合自拍直觉的镜像画面。
                frame = cv2.flip(frame, 1)
                # OpenCV使用BGR，MediaPipe要求RGB，因此交换颜色通道。
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # 把NumPy数组包装成MediaPipe识别的SRGB图像对象。
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

                # 计算程序启动至今经过的毫秒数。
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                # MediaPipe要求时间戳严格递增；同一毫秒内的帧至少加1。
                timestamp_ms = max(timestamp_ms, previous_timestamp_ms + 1)
                # 保存本帧时间戳供下一帧比较。
                previous_timestamp_ms = timestamp_ms
                # 执行手部检测，得到关键点、左右手分类等信息。
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                # 将检测结果直接画到原始BGR画面上。
                draw_result(frame, result)

                # 获取当前计时点。
                current_time = time.perf_counter()
                # 用相邻两帧的时间差计算瞬时FPS，并防止除以0。
                instantaneous_fps = 1.0 / max(current_time - previous_time, 1e-6)
                # 更新上一帧时间。
                previous_time = current_time
                # 对FPS做指数移动平均，降低显示数字的抖动。
                smoothed_fps = (
                    instantaneous_fps
                    if smoothed_fps == 0.0
                    else 0.9 * smoothed_fps + 0.1 * instantaneous_fps
                )

                # 在画面上显示FPS和当前检测到的手数。
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
                # 提醒用户红点所代表的关键点。
                cv2.putText(
                    frame,
                    "Red point = index fingertip (landmark 8)",
                    (20, 75),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                # 显示处理后的实时画面。
                cv2.imshow("Fingertip Tracker - Step 2", frame)

                # 等待1毫秒并读取按键编码。
                key = cv2.waitKey(1) & 0xFF
                # 按Q或Esc退出主循环。
                if key in (ord("q"), 27):
                    break
    finally:
        # 释放摄像头硬件句柄。
        camera.release()
        # 关闭OpenCV窗口。
        cv2.destroyAllWindows()


# 直接运行本文件时才调用main；被import时不会自动执行。
if __name__ == "__main__":
    main()
