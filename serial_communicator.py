import time

def main():
    """主函数"""
    try:
        import serial
        import serial.tools.list_ports
        
        def list_available_ports():
            """列出所有可用的串口"""
            ports = list(serial.tools.list_ports.comports())
            available_ports = []
            for port in ports:
                available_ports.append(port.device)
            return available_ports
        
        def auto_detect_com_port():
            """自动检测可用的COM口"""
            ports = list_available_ports()
            if not ports:
                print("未找到可用的COM口")
                return None
            print(f"找到可用的COM口: {ports}")
            # 返回第一个可用的COM口
            return ports[0]
        
        def send_hex_data(ser, hex_data):
            """发送16进制数据"""
            try:
                # 输入验证
                if not hex_data:
                    raise ValueError("十六进制数据不能为空")
                # 检查是否为有效十六进制字符串（允许空格）
                if not all(c in '0123456789ABCDEFabcdef ' for c in hex_data):
                    raise ValueError("无效的十六进制数据")
                # 将十六进制字符串转换为字节
                byte_data = bytes.fromhex(hex_data)
                ser.write(byte_data)
                print(f"已发送16进制数据: {hex_data}")
            except Exception as e:
                print(f"发送16进制数据失败: {e}")
        
        def send_help_command(ser):
            """发送help指令"""
            try:
                # 发送help指令，添加回车换行
                ser.write(b"help\r\n")
                print("已发送help指令")
                # 尝试读取响应
                time.sleep(0.5)  # 等待响应
                if ser.in_waiting > 0:
                    response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    print(f"收到响应: {response}")
            except Exception as e:
                print(f"发送help指令失败: {e}")
        
        # 自动检测COM口
        com_port = auto_detect_com_port()
        if not com_port:
            return
        
        # 配置串口参数
        baud_rate = 115200
        timeout = 1
        
        try:
            # 打开串口
            ser = serial.Serial(
                port=com_port,
                baudrate=baud_rate,
                timeout=timeout
            )
            print(f"已打开串口: {com_port}，比特率: {baud_rate}")
            
            # 示例：发送16进制数据
            hex_data = "01 02 03 04 05"
            send_hex_data(ser, hex_data)
            
            # 发送help指令
            send_help_command(ser)
            
            # 关闭串口
            ser.close()
            print("串口已关闭")
            
        except Exception as e:
            print(f"串口操作失败: {e}")
    
    except ImportError:
        print("错误: 未找到serial模块，请安装pyserial库。")
        print("安装命令: pip install pyserial")

if __name__ == "__main__":
    main()
