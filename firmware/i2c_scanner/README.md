# ESP32-S3 / PCA9685 I²C扫描程序

该固件只扫描I²C总线，不会生成任何舵机PWM信号。它用于独立验证ESP32和PCA9685之间的四根逻辑接线。

## 接线

- ESP32-S3 `3V3` → PCA9685 `VCC`
- ESP32-S3 `GND` → PCA9685 `GND`
- ESP32-S3 `GPIO8` → PCA9685 `SDA`
- ESP32-S3 `GPIO9` → PCA9685 `SCL`
- PCA9685 `OE`和控制接口的`V+`保持不连接

首次测试时拔下全部舵机，并保持外部5V舵机电源关闭。

## 预期结果

串口输出应至少包含`Found device at 0x40`。同时出现`0x70`也正常，它是PCA9685默认启用的全呼叫地址。

源代码中的中文注释逐项解释了地址扫描范围、Wire返回码、setup/loop和串口波特率。
