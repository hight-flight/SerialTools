import serial
import serial.tools.list_ports
import time

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
        print(f"转换后的字节数据: {byte_data}")
        print(f"已发送16进制数据: {hex_data}")
    except Exception as e:
        print(f"发送16进制数据失败: {e}")

# 测试函数
def test_send_hex_data():
    print("测试send_hex_data函数的输入验证")
    print("=" * 50)
    
    # 测试用例1: 有效十六进制数据（带空格）
    print("测试用例1: 有效十六进制数据（带空格）")
    send_hex_data(None, "01 02 03 04 05")
    print()
    
    # 测试用例2: 有效十六进制数据（无空格）
    print("测试用例2: 有效十六进制数据（无空格）")
    send_hex_data(None, "0102030405")
    print()
    
    # 测试用例3: 有效十六进制数据（小写）
    print("测试用例3: 有效十六进制数据（小写）")
    send_hex_data(None, "a1 b2 c3 d4 e5")
    print()
    
    # 测试用例4: 空值测试
    print("测试用例4: 空值测试")
    send_hex_data(None, "")
    print()
    
    # 测试用例5: 无效十六进制字符
    print("测试用例5: 无效十六进制字符")
    send_hex_data(None, "01 02 03 04 0g")
    print()
    
    # 测试用例6: None值测试
    print("测试用例6: None值测试")
    send_hex_data(None, None)
    print()

if __name__ == "__main__":
    test_send_hex_data()
