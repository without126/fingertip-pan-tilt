// Arduino.h提供setup、loop、Serial和delay等Arduino框架功能。
#include <Arduino.h>
// Wire.h提供ESP32的I²C通信接口。
#include <Wire.h>

// 匿名命名空间限制常量和辅助函数只在当前文件中使用。
namespace {
// ESP32连接PCA9685 SDA的GPIO编号。
constexpr int kSdaPin = 8;
// ESP32连接PCA9685 SCL的GPIO编号。
constexpr int kSclPin = 9;

// PCA9685出厂默认的7位I²C设备地址。
constexpr uint8_t kPcaAddress = 0x40;
// MODE1寄存器地址：控制睡眠、重启、自动递增等工作模式。
constexpr uint8_t kMode1 = 0x00;
// MODE2寄存器地址：控制PWM输出级的电气模式。
constexpr uint8_t kMode2 = 0x01;
// CH0的LED0_ON_L寄存器地址；每个通道连续使用4个寄存器。
constexpr uint8_t kLed0OnLow = 0x06;
// PRE_SCALE寄存器地址：决定所有通道共同使用的PWM频率。
constexpr uint8_t kPrescale = 0xFE;
// 根据GPIO7回读结果校准后的分频值；本模块使用127时更接近50Hz。
constexpr uint8_t kPrescaleValue = 127;

// 本次双舵机测试使用CH0作为第一个舵机通道。
constexpr uint8_t kServoChannel0 = 0;
// CH1作为第二个舵机通道。
constexpr uint8_t kServoChannel1 = 1;
// 实测312计数的方向正确但修正不足，因此再增加5计数，将偏移量扩大一倍。
constexpr uint16_t kChannel0CenterPulse = 317;
// CH1已经居中，因此继续使用307计数，即约1500微秒。
constexpr uint16_t kChannel1CenterPulse = 307;
// 约102个PCA9685计数对应约500微秒，也就是舵机约45度的单侧行程。
constexpr uint16_t kHalfRangeCounts = 102;
// CH0围绕校准中位317向低端移动102计数，得到约1050微秒。
constexpr uint16_t kChannel0LowPulse = kChannel0CenterPulse - kHalfRangeCounts;
// CH0围绕校准中位317向高端移动102计数，得到约2050微秒。
constexpr uint16_t kChannel0HighPulse = kChannel0CenterPulse + kHalfRangeCounts;
// CH1围绕中位307向低端移动102计数，得到约1000微秒。
constexpr uint16_t kChannel1LowPulse = kChannel1CenterPulse - kHalfRangeCounts;
// CH1围绕中位307向高端移动102计数，得到约2000微秒。
constexpr uint16_t kChannel1HighPulse = kChannel1CenterPulse + kHalfRangeCounts;
// 每次只改变1个计数，避免舵机突然跳到满幅端点。
constexpr int kPulseStep = 1;
// 每个小步之间等待20毫秒；相比上一轮60毫秒，运动速度约提高到3倍。
constexpr unsigned long kStepDelayMs = 20;
// 开始运动前在两个校准中位停留2.5秒。
constexpr unsigned long kCenterHoldMs = 2500;
// CH0结束后等待1.5秒，再开始CH1测试。
constexpr unsigned long kBetweenAxesDelayMs = 1500;

// 只有setup完成全部检测后才设为true；一轮测试结束后会恢复false。
bool gTestReady = false;

// 向PCA9685的单个8位寄存器写入一个8位值。
bool writeRegister(uint8_t reg, uint8_t value) {
  // 开始一次发往0x40的I²C写事务。
  Wire.beginTransmission(kPcaAddress);
  // 第一个字节告诉PCA9685要访问哪个寄存器。
  Wire.write(reg);
  // 第二个字节是要写入该寄存器的值。
  Wire.write(value);
  // 结束事务；返回0代表成功，因此表达式结果为true。
  return Wire.endTransmission() == 0;
}

// 从PCA9685的单个8位寄存器读取数据，并通过引用参数value带回结果。
bool readRegister(uint8_t reg, uint8_t& value) {
  // 开始一次发往PCA9685的I²C事务。
  Wire.beginTransmission(kPcaAddress);
  // 先写入希望读取的寄存器地址。
  Wire.write(reg);
  // false表示发送“重复起始条件”而不是完全释放总线，便于紧接着读取。
  if (Wire.endTransmission(false) != 0) {
    // 非0表示通信失败，立即返回false。
    return false;
  }

  // 请求PCA9685返回1个字节；返回值不是1说明读取失败。
  if (Wire.requestFrom(kPcaAddress, static_cast<uint8_t>(1)) != 1) {
    return false;
  }

  // 取出Wire接收缓冲区里的一个字节，写入调用者传来的value变量。
  value = Wire.read();
  // 整个读取流程成功。
  return true;
}

// 设置一个指定通道在4096计数周期中的开启时刻和关闭时刻。
bool setPwm(uint8_t channel, uint16_t onCount, uint16_t offCount) {
  // 每个通道占4个寄存器，因此通道起始地址等于0x06加4倍通道编号。
  const uint8_t baseRegister = kLed0OnLow + 4 * channel;
  // 开始写PCA9685。
  Wire.beginTransmission(kPcaAddress);
  // 指定从当前通道的LEDn_ON_L寄存器开始写入。
  Wire.write(baseRegister);
  // 写入开启计数值的低8位。
  Wire.write(onCount & 0xFF);
  // 右移8位取得高位；0x1F保留计数高4位和FULL_ON控制位。
  Wire.write((onCount >> 8) & 0x1F);
  // 写入关闭计数值的低8位。
  Wire.write(offCount & 0xFF);
  // 写入关闭计数高位；offCount=0x1000时会设置FULL_OFF位。
  Wire.write((offCount >> 8) & 0x1F);
  // 发送数据并根据返回码判断是否成功。
  return Wire.endTransmission() == 0;
}

// 逐个把相同的PWM参数写入CH0～CH15，完全绕开ALL_LED寄存器。
bool setEveryChannelPwm(uint16_t onCount, uint16_t offCount) {
  // channel从0递增到15，覆盖PCA9685的全部16个输出通道。
  for (uint8_t channel = 0; channel < 16; ++channel) {
    // 任一通道写入失败就立即返回false，不假装本轮更新成功。
    if (!setPwm(channel, onCount, offCount)) {
      return false;
    }
  }
  // 全部16个通道都收到I²C应答时返回true。
  return true;
}

// 让指定通道从startPulse逐个计数缓慢移动到targetPulse。
bool moveChannelSlowly(uint8_t channel,
                       uint16_t startPulse,
                       uint16_t targetPulse) {
  // 使用有符号整数，确保向较小脉宽移动时不会产生无符号数下溢。
  int currentPulse = static_cast<int>(startPulse);
  // 将目标值转换为同一类型，方便循环比较。
  const int target = static_cast<int>(targetPulse);
  // 目标较大时每步加1，否则每步减1。
  const int direction = target > currentPulse ? kPulseStep : -kPulseStep;

  // 逐步逼近目标端点，而不是一次写入造成突然运动。
  while (currentPulse != target) {
    // 先计算下一个脉宽计数。
    currentPulse += direction;
    // 只修改正在测试的通道，另一个通道保持自己的校准中位。
    if (!setPwm(channel, 0, static_cast<uint16_t>(currentPulse))) {
      // I²C写入失败时立即退出，由调用者关闭全部输出。
      return false;
    }
    // 给舵机留出跟随每个小步的时间。
    delay(kStepDelayMs);
  }

  // 成功到达目标端点。
  return true;
}

// 运动过程中出现I²C错误时停止测试并关闭所有通道。
void stopTestAfterError() {
  // FULL_OFF关闭CH0～CH15的PWM输出。
  setEveryChannelPwm(0, 0x1000);
  // 阻止loop再次启动运动。
  gTestReady = false;
  // 在串口中明确报告故障和断电要求。
  Serial.println("ERROR: I2C write failed during motion; all channels disabled.");
  Serial.println("Turn off the external 5 V supply now.");
}

// 将PCA9685安全配置为约50Hz，并让CH0和CH1都从中位脉冲开始输出。
bool configurePca9685ForServos() {
  // 先进行一次空写事务，用来确认0x40确实存在。
  Wire.beginTransmission(kPcaAddress);
  // 没有收到应答就立即失败，不继续修改任何寄存器。
  if (Wire.endTransmission() != 0) {
    return false;
  }

  // oldMode用于保存MODE1原值，避免无意丢失已有设置。
  uint8_t oldMode = 0;
  // 读取MODE1失败时停止初始化。
  if (!readRegister(kMode1, oldMode)) {
    return false;
  }

  // 0x6F会同时清除RESTART(bit7)和SLEEP(bit4)，避免继承旧的睡眠状态。
  // 再设置AI(bit5)，使后续连续写4个PWM寄存器时地址能够自动递增。
  const uint8_t awakeMode = (oldMode & 0x6F) | 0x20;
  // 修改PRE_SCALE前必须暂时设置SLEEP位，让内部振荡器停止。
  const uint8_t sleepMode = awakeMode | 0x10;
  // 依次进入睡眠、设置50Hz分频、设置推挽输出，然后明确退出睡眠。
  if (!writeRegister(kMode1, sleepMode) ||
      // 使用回读实测校准值127，让这块模块的实际频率更接近50Hz。
      !writeRegister(kPrescale, kPrescaleValue) ||
      // MODE2=0x04设置OUTDRV位，使用适合舵机信号的推挽输出。
      !writeRegister(kMode2, 0x04) ||
      // 写入已明确清除SLEEP位的awakeMode，启动内部振荡器。
      !writeRegister(kMode1, awakeMode)) {
    // 任意一步失败都返回false。
    return false;
  }

  // 数据手册要求退出睡眠后等待至少500微秒，这里保守等待5毫秒。
  delay(5);
  // 在保持SLEEP=0的前提下设置RESTART位，让新频率配置正式生效。
  if (!writeRegister(kMode1, awakeMode | 0x80)) {
    return false;
  }

  // 再次读取MODE1，确认硬件没有停留在SLEEP状态。
  uint8_t verifiedMode = 0;
  // 读取失败或bit4仍为1都意味着PWM振荡器没有可靠启动。
  if (!readRegister(kMode1, verifiedMode) || (verifiedMode & 0x10) != 0) {
    return false;
  }

  // offCount=0x1000设置FULL_OFF位，逐个强制关闭全部16个通道。
  if (!setEveryChannelPwm(0, 0x1000)) {
    return false;
  }

  // 先启用CH0试调后的中位脉冲；写入失败就不继续启用第二个通道。
  if (!setPwm(kServoChannel0, 0, kChannel0CenterPulse)) {
    return false;
  }
  // 再启用CH1原有的中位脉冲；CH2～CH15继续保持FULL_OFF。
  return setPwm(kServoChannel1, 0, kChannel1CenterPulse);
}
// 结束匿名命名空间。
}  // namespace

// setup在ESP32上电或复位后只运行一次。
void setup() {
  // 初始化USB-UART串口，供VS Code串口监视器读取信息。
  Serial.begin(115200);
  // 等待1.5秒，让电脑端串口准备完成。
  delay(1500);

  // 初始化I²C：SDA使用GPIO8，SCL使用GPIO9。
  Wire.begin(kSdaPin, kSclPin);
  // 打印当前固件名称。
  Serial.println("Safe PCA9685 final dual-servo maximum-range test");
  // 强调上传期间不得打开舵机外部5V电源。
  Serial.println("External 5 V must remain OFF during upload.");

  // 执行PCA9685检测和安全配置；前面的!表示“配置没有成功”。
  if (!configurePca9685ForServos()) {
    // 明确报告错误原因范围。
    Serial.println("ERROR: PCA9685 not found or configuration failed.");
    // 出错时禁止用户打开舵机电源。
    Serial.println("Do not turn on the external 5 V supply.");
    // 从setup提前返回；loop仍会运行，但不会产生舵机控制操作。
    return;
  }

  // 以下信息只会在所有I²C写操作成功后打印。
  Serial.println("PCA9685 detected and configured at 50 Hz.");
  // 读取MODE1和MODE2，确认自动递增、输出驱动模式等配置真正写入芯片。
  uint8_t mode1Value = 0;
  uint8_t mode2Value = 0;
  // 两个模式寄存器都成功读取时打印十六进制值；MODE1的SLEEP位应为0。
  if (readRegister(kMode1, mode1Value) && readRegister(kMode2, mode2Value)) {
    Serial.printf("Mode registers: MODE1=0x%02X, MODE2=0x%02X (SLEEP expected 0, MODE2 expected 0x04)\n",
                  mode1Value, mode2Value);
  } else {
    // 模式寄存器读回失败意味着I²C状态不可靠，不应继续接舵机。
    Serial.println("ERROR: failed to read MODE1/MODE2 registers.");
    return;
  }
  // 打印两个轴各自的校准中位和端点，便于核对本轮使用的实际参数。
  Serial.println("CH0: center=317, endpoints=215/419 (about 90 degrees total).");
  Serial.println("CH1: center=307, endpoints=205/409 (about 90 degrees total).");
  // 打印本轮速度设置，便于确认上传的是20毫秒快速测试版本。
  Serial.println("Motion step delay=20 ms (about 3x the previous test speed).");
  // 明确剩余通道均关闭。
  Serial.println("CH2-CH15 are disabled.");
  // 所有检测通过后允许loop执行且只执行一轮测试。
  gTestReady = true;
  // 看到该标志且完成机械检查后，才允许接通外部5V。
  Serial.println("READY_FOR_FINAL_MAX_RANGE_TEST");
}

// Arduino框架会反复调用loop；本程序只完成一轮双轴满幅测试。
void loop() {
  // setup失败或测试已经完成时不再执行运动。
  if (!gTestReady) {
    delay(1000);
    return;
  }

  // 每轮开始先明确把两个轴放在各自校准后的中位。
  if (!setPwm(kServoChannel0, 0, kChannel0CenterPulse) ||
      !setPwm(kServoChannel1, 0, kChannel1CenterPulse)) {
    stopTestAfterError();
    return;
  }
  // 留出观察中位及接通外部电源的时间。
  Serial.println("Both axes at their calibrated centers; CH0 starts in 2.5 seconds.");
  delay(kCenterHoldMs);

  // CH0执行：校准中位 -> 高端 -> 低端 -> 校准中位。
  Serial.println("CH0_MAX_RANGE_TEST_START");
  if (!moveChannelSlowly(kServoChannel0, kChannel0CenterPulse, kChannel0HighPulse) ||
      !moveChannelSlowly(kServoChannel0, kChannel0HighPulse, kChannel0LowPulse) ||
      !moveChannelSlowly(kServoChannel0, kChannel0LowPulse, kChannel0CenterPulse)) {
    stopTestAfterError();
    return;
  }
  // CH0回到317计数的校准中位；CH1此前一直保持307计数。
  Serial.println("CH0_MAX_RANGE_TEST_COMPLETE_AND_CENTERED");
  delay(kBetweenAxesDelayMs);

  // CH1执行：原中位 -> 高端 -> 低端 -> 原中位。
  Serial.println("CH1_MAX_RANGE_TEST_START");
  if (!moveChannelSlowly(kServoChannel1, kChannel1CenterPulse, kChannel1HighPulse) ||
      !moveChannelSlowly(kServoChannel1, kChannel1HighPulse, kChannel1LowPulse) ||
      !moveChannelSlowly(kServoChannel1, kChannel1LowPulse, kChannel1CenterPulse)) {
    stopTestAfterError();
    return;
  }
  // CH1已经回中；此时两个轴都保持各自校准中位。
  Serial.println("CH1_MAX_RANGE_TEST_COMPLETE_AND_CENTERED");
  // 关闭运行标志，防止满幅测试反复执行。
  gTestReady = false;
  Serial.println("FINAL_MAX_RANGE_TEST_FINISHED_BOTH_AXES_HOLDING_CENTER");
}
