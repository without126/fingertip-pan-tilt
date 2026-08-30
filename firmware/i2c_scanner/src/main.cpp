// Arduino.h提供setup、loop、delay和Serial等Arduino框架功能。
#include <Arduino.h>
// Wire.h提供I²C通信功能；ESP32通过它与PCA9685通信。
#include <Wire.h>

// 匿名命名空间让其中的常量只在当前源文件内可见，避免名称冲突。
namespace {
// ESP32-S3上连接PCA9685 SDA线的GPIO编号。
constexpr int kSdaPin = 8;
// ESP32-S3上连接PCA9685 SCL线的GPIO编号。
constexpr int kSclPin = 9;
// 两次完整扫描之间等待2000毫秒，也就是2秒。
constexpr unsigned long kScanIntervalMs = 2000;
// 结束匿名命名空间；常量仍可被本文件后面的函数使用。
}  // namespace

// 扫描1~126的全部合法7位I²C地址，并把应答设备打印到串口。
void scanI2cBus() {
  // found记录本轮一共发现了多少个I²C地址；byte是uint8_t的别名。
  byte found = 0;

  // 在串口监视器中打印本轮扫描开始提示。
  Serial.println("Scanning I2C bus...");
  // 0是广播保留地址，127也是保留地址，所以只扫描1到126。
  for (byte address = 1; address < 127; ++address) {
    // 告诉Wire库接下来要尝试与哪个I²C地址通信。
    Wire.beginTransmission(address);
    // 不发送正文，直接结束；返回值可以判断目标是否应答。
    const byte error = Wire.endTransmission();

    // 返回0表示目标地址正常应答。
    if (error == 0) {
      // %02X把地址显示为两位十六进制，例如0x40。
      Serial.printf("Found device at 0x%02X\n", address);
      // 发现设备后把计数加1。
      ++found;
    // 返回4表示Wire库遇到了未归类的底层通信错误。
    } else if (error == 4) {
      // 打印发生未知错误的地址，方便排查总线问题。
      Serial.printf("Unknown error at 0x%02X\n", address);
    }
  }

  // 扫完整条总线后，如果计数仍为0，说明没有任何设备应答。
  if (found == 0) {
    // 常见原因是VCC/GND/SDA/SCL接错或PCA9685没有逻辑供电。
    Serial.println("No I2C device found.");
  // found不为0则输出本轮发现的地址数量。
  } else {
    // %u用于显示无符号整数。
    Serial.printf("Scan complete: %u device(s) found.\n", found);
  }
  // 额外输出一个空行，把连续两轮扫描分隔开。
  Serial.println();
}

// setup只在ESP32上电或复位后执行一次。
void setup() {
  // 初始化UART串口，波特率必须与PlatformIO监视器的115200一致。
  Serial.begin(115200);
  // 等待1.5秒，让USB串口和电脑端监视器有时间准备好。
  delay(1500);

  // 初始化I²C控制器，并明确指定SDA=GPIO8、SCL=GPIO9。
  Wire.begin(kSdaPin, kSclPin);
  // 打印程序名称。
  Serial.println("ESP32-S3 safe I2C scanner");
  // 打印实际使用的两个GPIO；两个\n会在末尾再留一个空行。
  Serial.printf("SDA=GPIO%d, SCL=GPIO%d\n\n", kSdaPin, kSclPin);
}

// setup结束后，Arduino框架会反复调用loop。
void loop() {
  // 执行一次完整的I²C地址扫描。
  scanI2cBus();
  // 等待2秒再进入下一轮，避免串口输出过快。
  delay(kScanIntervalMs);
}
