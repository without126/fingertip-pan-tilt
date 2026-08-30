// Arduino.h提供setup、loop、millis、Serial等Arduino框架功能。
#include <Arduino.h>
// Wire.h提供ESP32与PCA9685之间的I²C通信功能。
#include <Wire.h>
// cstdlib提供strtol，用于安全地把串口文本转换为整数。
#include <cstdlib>
// cstring提供strcmp和strncmp，用于识别串口命令。
#include <cstring>

// 匿名命名空间让下面的常量、变量和辅助函数只在当前文件中可见。
namespace {
// ESP32连接PCA9685 SDA的GPIO编号。
constexpr int kSdaPin = 8;
// ESP32连接PCA9685 SCL的GPIO编号。
constexpr int kSclPin = 9;

// PCA9685出厂默认的7位I²C地址。
constexpr uint8_t kPcaAddress = 0x40;
// MODE1寄存器控制睡眠、重启和寄存器自动递增。
constexpr uint8_t kMode1 = 0x00;
// MODE2寄存器控制PWM输出级的电气模式。
constexpr uint8_t kMode2 = 0x01;
// CH0第一个PWM寄存器LED0_ON_L的地址。
constexpr uint8_t kLed0OnLow = 0x06;
// PRE_SCALE寄存器决定16个通道共同使用的PWM频率。
constexpr uint8_t kPrescale = 0xFE;
// 这块PCA9685经回读校准后使用127最接近舵机需要的50Hz。
constexpr uint8_t kPrescaleValue = 127;

// CH0连接云台的第一个舵机。
constexpr uint8_t kServoChannel0 = 0;
// CH1连接云台的第二个舵机。
constexpr uint8_t kServoChannel1 = 1;
// CH0经过实物校准后的中位计数。
constexpr int kChannel0Center = 317;
// CH1经过实物校准后的中位计数。
constexpr int kChannel1Center = 307;
// CH0约90度安全范围的低端。
constexpr int kChannel0Minimum = 215;
// CH0约90度安全范围的高端。
constexpr int kChannel0Maximum = 419;
// CH1约90度安全范围的低端。
constexpr int kChannel1Minimum = 205;
// CH1约90度安全范围的高端。
constexpr int kChannel1Maximum = 409;

// 每20毫秒更新一次舵机，等价于50Hz控制循环。
constexpr unsigned long kMotionUpdateIntervalMs = 20;
// 每次最多移动2个PCA9685计数，限制突然运动的速度。
constexpr int kMaximumStepPerUpdate = 2;
// 超过700毫秒收不到电脑命令时，目标自动改回两个校准中位。
constexpr unsigned long kCommandTimeoutMs = 700;
// 一条命令最多占47个可见字符，最后一个字节留给字符串结束符\0。
constexpr size_t kSerialBufferSize = 48;

// 串口接收缓冲区暂存尚未遇到换行符的一条命令。
char gSerialBuffer[kSerialBufferSize] = {};
// 记录缓冲区当前已有多少个字符。
size_t gSerialLength = 0;
// CH0当前已经输出的PWM计数，启动时位于校准中位。
int gCurrentChannel0 = kChannel0Center;
// CH1当前已经输出的PWM计数。
int gCurrentChannel1 = kChannel1Center;
// CH0希望逐步到达的目标计数。
int gTargetChannel0 = kChannel0Center;
// CH1希望逐步到达的目标计数。
int gTargetChannel1 = kChannel1Center;
// 记录最近一条有效运动命令到达的millis时间。
unsigned long gLastCommandMs = 0;
// true表示至少收到过一条尚未超时的运动命令。
bool gHasLiveCommand = false;
// true表示PCA9685初始化成功且仍允许执行控制。
bool gHardwareReady = false;
// true表示CH0和CH1当前没有被FULL_OFF关闭。
bool gOutputsEnabled = false;
// 记录上一次运动更新时刻，用来实现固定周期的非阻塞控制。
unsigned long gLastMotionUpdateMs = 0;

// 将value限制在minimum与maximum之间。
int clampValue(int value, int minimum, int maximum) {
  // 小于下限时返回下限。
  if (value < minimum) {
    return minimum;
  }
  // 大于上限时返回上限。
  if (value > maximum) {
    return maximum;
  }
  // 已经位于安全范围内时保持原值。
  return value;
}

// 向PCA9685的一个8位寄存器写入一个8位值。
bool writeRegister(uint8_t reg, uint8_t value) {
  // 开始一次发往0x40的I²C写事务。
  Wire.beginTransmission(kPcaAddress);
  // 第一个字节指定要写入的寄存器地址。
  Wire.write(reg);
  // 第二个字节是实际数据。
  Wire.write(value);
  // endTransmission返回0表示PCA9685正确应答。
  return Wire.endTransmission() == 0;
}

// 从PCA9685的一个8位寄存器读取数据。
bool readRegister(uint8_t reg, uint8_t& value) {
  // 先向PCA9685写入希望读取的寄存器地址。
  Wire.beginTransmission(kPcaAddress);
  Wire.write(reg);
  // false发送重复起始条件，便于紧接着执行读取。
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  // 请求返回一个字节；数量不是1表示读取失败。
  if (Wire.requestFrom(kPcaAddress, static_cast<uint8_t>(1)) != 1) {
    return false;
  }

  // 取出接收缓冲区中的字节并交给调用者。
  value = Wire.read();
  return true;
}

// 设置一个PCA9685通道在4096计数周期内的开启和关闭时刻。
bool setPwm(uint8_t channel, uint16_t onCount, uint16_t offCount) {
  // 每个通道连续占4个寄存器，因此起始地址为0x06加4倍通道号。
  const uint8_t baseRegister = kLed0OnLow + 4 * channel;
  // 开始I²C连续写入。
  Wire.beginTransmission(kPcaAddress);
  // 指定当前通道LEDn_ON_L寄存器。
  Wire.write(baseRegister);
  // 写入开启计数的低8位。
  Wire.write(onCount & 0xFF);
  // 写入开启计数的高4位以及FULL_ON控制位。
  Wire.write((onCount >> 8) & 0x1F);
  // 写入关闭计数的低8位。
  Wire.write(offCount & 0xFF);
  // 写入关闭计数高位；0x1000会在这里设置FULL_OFF位。
  Wire.write((offCount >> 8) & 0x1F);
  // 返回本次I²C事务是否成功。
  return Wire.endTransmission() == 0;
}

// 给CH0到CH15逐个写入相同的PWM参数。
bool setEveryChannelPwm(uint16_t onCount, uint16_t offCount) {
  // channel依次取0到15，覆盖全部16个输出。
  for (uint8_t channel = 0; channel < 16; ++channel) {
    // 任一通道写入失败就立即报告失败。
    if (!setPwm(channel, onCount, offCount)) {
      return false;
    }
  }
  // 全部通道写入成功。
  return true;
}

// 安全初始化PCA9685，并让两个舵机从各自校准中位开始。
bool configurePca9685ForServos() {
  // 空事务用于确认0x40设备确实存在。
  Wire.beginTransmission(kPcaAddress);
  if (Wire.endTransmission() != 0) {
    return false;
  }

  // 保存MODE1原值，以便只修改我们关心的控制位。
  uint8_t oldMode = 0;
  if (!readRegister(kMode1, oldMode)) {
    return false;
  }

  // 0x6F清除RESTART和SLEEP位；0x20开启寄存器地址自动递增。
  const uint8_t awakeMode = (oldMode & 0x6F) | 0x20;
  // 修改分频寄存器之前必须先让振荡器进入睡眠。
  const uint8_t sleepMode = awakeMode | 0x10;
  // 依次设置睡眠、分频、推挽输出和唤醒状态。
  if (!writeRegister(kMode1, sleepMode) ||
      !writeRegister(kPrescale, kPrescaleValue) ||
      !writeRegister(kMode2, 0x04) ||
      !writeRegister(kMode1, awakeMode)) {
    return false;
  }

  // 数据手册要求退出睡眠后等待至少500微秒，这里保守等待5毫秒。
  delay(5);
  // 设置RESTART位，使新的分频配置正式生效，同时保持SLEEP为0。
  if (!writeRegister(kMode1, awakeMode | 0x80)) {
    return false;
  }

  // 回读MODE1并确认SLEEP位确实已经清除。
  uint8_t verifiedMode = 0;
  if (!readRegister(kMode1, verifiedMode) || (verifiedMode & 0x10) != 0) {
    return false;
  }

  // 先将全部16个通道置于FULL_OFF，避免旧寄存器值导致意外动作。
  if (!setEveryChannelPwm(0, 0x1000)) {
    return false;
  }
  // 开启CH0并输出其校准中位。
  if (!setPwm(kServoChannel0, 0, kChannel0Center)) {
    return false;
  }
  // 开启CH1并输出其校准中位；CH2到CH15继续关闭。
  if (!setPwm(kServoChannel1, 0, kChannel1Center)) {
    return false;
  }

  // 软件状态与刚刚写入硬件的两个中位保持一致。
  gCurrentChannel0 = kChannel0Center;
  gCurrentChannel1 = kChannel1Center;
  gTargetChannel0 = kChannel0Center;
  gTargetChannel1 = kChannel1Center;
  gOutputsEnabled = true;
  return true;
}

// 把current向target移动，但单次变化不超过maximumStep。
int moveToward(int current, int target, int maximumStep) {
  // 目标在当前值上方时向上移动。
  if (target > current) {
    // 剩余距离小于步长时直接到达目标，否则只前进maximumStep。
    return current + min(target - current, maximumStep);
  }
  // 目标在当前值下方时向下移动。
  if (target < current) {
    return current - min(current - target, maximumStep);
  }
  // 已到达目标时保持不变。
  return current;
}

// 在发生硬件通信错误时关闭全部PWM，并禁止后续运动。
void enterHardwareFault() {
  // 尽最大努力关闭全部通道；即使这一步失败也继续设置软件故障状态。
  setEveryChannelPwm(0, 0x1000);
  // 禁止loop继续驱动PCA9685。
  gHardwareReady = false;
  gOutputsEnabled = false;
  // 向电脑报告故障，要求关闭舵机独立电源。
  Serial.println("ERROR,I2C_FAILURE,TURN_OFF_EXTERNAL_5V");
}

// 如果输出曾被DISABLE命令关闭，重新以当前位置启用CH0和CH1。
bool enableOutputsAtCurrentPosition() {
  // 已经启用时不重复写寄存器。
  if (gOutputsEnabled) {
    return true;
  }
  // 分别恢复两个通道的当前位置。
  if (!setPwm(kServoChannel0, 0, static_cast<uint16_t>(gCurrentChannel0)) ||
      !setPwm(kServoChannel1, 0, static_cast<uint16_t>(gCurrentChannel1))) {
    return false;
  }
  // 两次写入都成功后更新软件状态。
  gOutputsEnabled = true;
  return true;
}

// 解析形如T,317,307的目标命令，成功时通过引用参数返回两个整数。
bool parseTargetCommand(const char* line, int& channel0, int& channel1) {
  // 命令必须以字母T和逗号开头。
  if (strncmp(line, "T,", 2) != 0) {
    return false;
  }

  // endPointer会由strtol指向第一个未被解析的字符。
  char* endPointer = nullptr;
  // 从T,之后开始解析CH0整数。
  const long parsedChannel0 = strtol(line + 2, &endPointer, 10);
  // CH0后面必须紧跟一个逗号。
  if (endPointer == line + 2 || *endPointer != ',') {
    return false;
  }

  // 从第二个逗号之后解析CH1整数。
  const char* channel1Start = endPointer + 1;
  const long parsedChannel1 = strtol(channel1Start, &endPointer, 10);
  // CH1必须至少包含一个数字，而且后面必须直接到字符串末尾。
  if (endPointer == channel1Start || *endPointer != '\0') {
    return false;
  }

  // 转成int交给调用者；之后还会执行独立的安全范围限制。
  channel0 = static_cast<int>(parsedChannel0);
  channel1 = static_cast<int>(parsedChannel1);
  return true;
}

// 处理一条已经去除换行符的完整串口命令。
void processSerialLine(const char* line) {
  // PING即使在PCA9685故障时也允许响应，方便判断ESP32串口本身是否正常。
  if (strcmp(line, "PING") == 0) {
    Serial.println("PONG");
    return;
  }

  // 除PING外，任何可能改变输出的命令都必须等待硬件初始化成功。
  if (!gHardwareReady) {
    Serial.println("ERROR,HARDWARE_NOT_READY");
    return;
  }

  // CENTER命令让两个轴以限速方式回到各自校准中位。
  if (strcmp(line, "CENTER") == 0) {
    gTargetChannel0 = kChannel0Center;
    gTargetChannel1 = kChannel1Center;
    gLastCommandMs = millis();
    gHasLiveCommand = true;
    if (!enableOutputsAtCurrentPosition()) {
      enterHardwareFault();
    }
    return;
  }

  // DISABLE命令立即关闭全部PWM；主要用于调试和紧急停止。
  if (strcmp(line, "DISABLE") == 0) {
    if (!setEveryChannelPwm(0, 0x1000)) {
      enterHardwareFault();
      return;
    }
    gOutputsEnabled = false;
    gHasLiveCommand = false;
    Serial.println("ACK,DISABLED");
    return;
  }

  // 尝试解析T,<CH0>,<CH1>格式的运动目标。
  int requestedChannel0 = 0;
  int requestedChannel1 = 0;
  if (!parseTargetCommand(line, requestedChannel0, requestedChannel1)) {
    // 未知或格式错误的命令不会改变任何舵机目标。
    Serial.println("ERROR,BAD_COMMAND");
    return;
  }

  // 无论电脑发送什么数字，都在ESP32端再次限制到已验证的安全范围。
  gTargetChannel0 = clampValue(
      requestedChannel0, kChannel0Minimum, kChannel0Maximum);
  gTargetChannel1 = clampValue(
      requestedChannel1, kChannel1Minimum, kChannel1Maximum);
  // 记录有效命令时间，供超时保护使用。
  gLastCommandMs = millis();
  gHasLiveCommand = true;
  // 如果此前收到DISABLE，则先从当前位置安全恢复输出。
  if (!enableOutputsAtCurrentPosition()) {
    enterHardwareFault();
  }
}

// 从Serial逐字节读取数据，并按换行符组成完整命令。
void receiveSerialCommands() {
  // 只要串口缓冲区还有数据，就继续读取，但整个过程不会主动等待。
  while (Serial.available() > 0) {
    // read返回一个字节；转换为char后便于判断文本字符。
    const char incoming = static_cast<char>(Serial.read());
    // Windows文本常使用\r\n，本程序忽略其中的\r。
    if (incoming == '\r') {
      continue;
    }
    // \n代表一条命令结束。
    if (incoming == '\n') {
      // 在已有字符末尾补\0，把缓冲区变成标准C字符串。
      gSerialBuffer[gSerialLength] = '\0';
      // 空行无需处理。
      if (gSerialLength > 0) {
        processSerialLine(gSerialBuffer);
      }
      // 清空长度，开始接收下一条命令。
      gSerialLength = 0;
      continue;
    }

    // 至少为末尾\0保留一个字节。
    if (gSerialLength < kSerialBufferSize - 1) {
      gSerialBuffer[gSerialLength] = incoming;
      ++gSerialLength;
    } else {
      // 命令过长时丢弃当前行，避免写出数组边界。
      gSerialLength = 0;
      Serial.println("ERROR,COMMAND_TOO_LONG");
    }
  }
}

// 每20毫秒把两个当前值向目标值推进最多2个计数。
void updateMotion(unsigned long nowMs) {
  // 尚未达到下一控制周期时立即返回，不使用阻塞delay。
  if (nowMs - gLastMotionUpdateMs < kMotionUpdateIntervalMs) {
    return;
  }
  // 保存本次更新时间。
  gLastMotionUpdateMs = nowMs;

  // 计算两个轴本周期的新位置。
  const int nextChannel0 = moveToward(
      gCurrentChannel0, gTargetChannel0, kMaximumStepPerUpdate);
  const int nextChannel1 = moveToward(
      gCurrentChannel1, gTargetChannel1, kMaximumStepPerUpdate);

  // CH0发生变化时才写I²C，减少无意义通信。
  if (nextChannel0 != gCurrentChannel0) {
    if (!setPwm(kServoChannel0, 0, static_cast<uint16_t>(nextChannel0))) {
      enterHardwareFault();
      return;
    }
    gCurrentChannel0 = nextChannel0;
  }

  // CH1发生变化时同样只写一次。
  if (nextChannel1 != gCurrentChannel1) {
    if (!setPwm(kServoChannel1, 0, static_cast<uint16_t>(nextChannel1))) {
      enterHardwareFault();
      return;
    }
    gCurrentChannel1 = nextChannel1;
  }
}
}  // namespace

// setup在ESP32上电或复位后只运行一次。
void setup() {
  // 初始化与电脑通信的串口。
  Serial.begin(115200);
  // 等待电脑串口准备，避免启动信息全部丢失。
  delay(1500);

  // 使用GPIO8和GPIO9初始化I²C总线。
  Wire.begin(kSdaPin, kSclPin);
  // 将I²C时钟提高到400kHz，缩短每次PWM更新的通信时间。
  Wire.setClock(400000);
  // 打印固件身份和安全提醒。
  Serial.println("Fingertip pan-tilt serial controller");
  Serial.println("External 5 V must remain OFF during upload.");

  // 初始化失败时不进入控制状态。
  if (!configurePca9685ForServos()) {
    Serial.println("ERROR,PCA9685_INITIALIZATION_FAILED");
    Serial.println("Do not turn on the external 5 V supply.");
    return;
  }

  // 只有初始化完整成功后才允许loop更新运动。
  gHardwareReady = true;
  gLastMotionUpdateMs = millis();
  // 告知Python安全范围、校准中位和可用状态。
  Serial.println("LIMITS,CH0=215:419,CH1=205:409");
  Serial.println("CENTERS,CH0=317,CH1=307");
  Serial.println("READY_FOR_FINGERTIP_COMMANDS");
}

// loop持续处理串口、超时保护和非阻塞舵机更新。
void loop() {
  // 即使硬件故障，也继续读取串口，方便PING或查看错误；但不再执行运动。
  receiveSerialCommands();
  if (!gHardwareReady) {
    delay(10);
    return;
  }

  // millis返回ESP32启动后的毫秒计数。
  const unsigned long nowMs = millis();
  // 一旦电脑命令超时，就只触发一次自动回中并清除活动标志。
  if (gHasLiveCommand && nowMs - gLastCommandMs > kCommandTimeoutMs) {
    gTargetChannel0 = kChannel0Center;
    gTargetChannel1 = kChannel1Center;
    gHasLiveCommand = false;
    Serial.println("WATCHDOG,RETURNING_TO_CENTER");
  }

  // 按固定周期、限速地向最新目标位置移动。
  updateMotion(nowMs);
}
