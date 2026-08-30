"""步骤1：读取摄像头画面，并在窗口中显示经过平滑处理的实时帧率。"""

# annotations 让类型注解延迟求值，可减少某些版本兼容问题。
from __future__ import annotations

# argparse 用来读取命令行参数，例如 --camera 1。
import argparse
# time 用来读取高精度时间，从而计算每秒处理多少帧。
import time

# OpenCV（导入名为 cv2）负责读取摄像头、绘制文字和显示窗口。
import cv2


def parse_args() -> argparse.Namespace:
    """定义并解析命令行参数，返回一个保存所有参数值的对象。"""

    # 创建参数解析器；description 会显示在 --help 帮助信息中。
    parser = argparse.ArgumentParser(description="Open a webcam and display FPS.")
    # --camera 指定摄像头编号；0 通常表示电脑的默认摄像头。
    parser.add_argument("--camera", type=int, default=0, help="Camera device index")
    # --width 请求摄像头输出的画面宽度，默认 1280 像素。
    parser.add_argument("--width", type=int, default=1280, help="Requested frame width")
    # --height 请求摄像头输出的画面高度，默认 720 像素。
    parser.add_argument("--height", type=int, default=720, help="Requested frame height")
    # 真正读取命令行，并把最终结果作为 Namespace 对象返回。
    return parser.parse_args()


def main() -> None:
    """程序入口：打开摄像头，循环读取画面，计算FPS并显示结果。"""

    # 获取用户传入的摄像头编号、宽度和高度。
    args = parse_args()
    # 使用 Windows 的 DirectShow 后端打开指定编号的摄像头。
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    # 请求摄像头使用指定宽度；摄像头不支持时可能采用最接近的值。
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    # 请求摄像头使用指定高度。
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    # isOpened() 为 False 说明摄像头初始化失败，不能继续读取。
    if not camera.isOpened():
        # 主动抛出异常，让终端显示明确的排查建议。
        raise RuntimeError(
            f"Cannot open camera {args.camera}. Try --camera 1 or check camera permission."
        )

    # 记录第一次计时点；perf_counter() 适合测量很短的时间间隔。
    previous_time = time.perf_counter()
    # 平滑FPS的初值为0，第一帧时会直接使用瞬时FPS。
    smoothed_fps = 0.0

    # try/finally 保证即使程序报错，也会在 finally 中释放摄像头。
    try:
        # 每次循环处理一帧，直到用户按 Q 或 Esc。
        while True:
            # camera.read() 返回“是否成功”和当前BGR画面。
            ok, frame = camera.read()
            # 摄像头已打开但没有读到帧时，立即报告错误。
            if not ok:
                raise RuntimeError("The camera opened, but no frame could be read.")

            # 参数1表示水平翻转，使画面像自拍镜子一样符合直觉。
            frame = cv2.flip(frame, 1)

            # 获取本帧到达时的高精度时间。
            current_time = time.perf_counter()
            # FPS=1/帧间隔；max(..., 1e-6) 防止极端情况下除以0。
            instantaneous_fps = 1.0 / max(current_time - previous_time, 1e-6)
            # 保存当前时间，下一帧会用它计算新的帧间隔。
            previous_time = current_time
            # 指数移动平均：保留90%旧值、加入10%新值，减少数字跳动。
            smoothed_fps = (
                instantaneous_fps
                if smoothed_fps == 0.0
                else 0.9 * smoothed_fps + 0.1 * instantaneous_fps
            )

            # 在图像左上角绘制FPS。
            cv2.putText(
                frame,  # 要修改的图像。
                f"FPS: {smoothed_fps:.1f}",  # 保留1位小数的显示文字。
                (20, 40),  # 文字左下角坐标(x, y)。
                cv2.FONT_HERSHEY_SIMPLEX,  # OpenCV内置字体。
                1.0,  # 字体缩放倍数。
                (0, 255, 0),  # BGR颜色：绿色。
                2,  # 线条粗细。
                cv2.LINE_AA,  # 抗锯齿，让文字边缘更平滑。
            )
            # 再绘制退出操作提示。
            cv2.putText(
                frame,
                "Press Q or ESC to quit",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),  # BGR三个通道均为255，即白色。
                2,
                cv2.LINE_AA,
            )
            # 创建或刷新名为“Fingertip Tracker - Step 1”的窗口。
            cv2.imshow("Fingertip Tracker - Step 1", frame)

            # 等待1毫秒并读取键盘；& 0xFF 保留最低8位以兼容Windows。
            key = cv2.waitKey(1) & 0xFF
            # ord("q")是q键编码，27是Esc键编码；任意一个都结束循环。
            if key in (ord("q"), 27):
                break
    finally:
        # 释放摄像头资源，避免下次运行时提示设备被占用。
        camera.release()
        # 关闭本程序创建的全部OpenCV窗口。
        cv2.destroyAllWindows()


# 只有直接运行本文件时条件才成立；被其他文件import时不会自动启动。
if __name__ == "__main__":
    # 调用上面定义的主函数。
    main()
