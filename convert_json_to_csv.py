#!/usr/bin/env python3
"""
JSON到CSV转换工具
将旧版本的多字符发送JSON配置文件转换为新的CSV格式
"""

import json
import csv
import os
import sys


def convert_json_to_csv(json_file, csv_file=None):
    """
    将JSON格式的多字符发送配置转换为CSV格式
    
    参数:
        json_file: 输入的JSON文件路径
        csv_file: 输出的CSV文件路径(可选,默认为同名.csv文件)
    """
    # 如果未指定输出文件,使用输入文件名+.csv
    if csv_file is None:
        base_name = os.path.splitext(json_file)[0]
        csv_file = base_name + '.csv'
    
    try:
        # 读取JSON文件
        print(f"正在读取JSON文件: {json_file}")
        with open(json_file, 'r', encoding='utf-8') as f:
            items = json.load(f)
        
        print(f"找到 {len(items)} 个项目")
        
        # 写入CSV文件
        print(f"正在写入CSV文件: {csv_file}")
        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['hex', 'string', 'button_text', 'delay', 'order'])
            writer.writeheader()
            
            for i, item in enumerate(items):
                # 处理可能的键名差异 (text vs string)
                csv_item = {
                    'hex': item.get('hex', False),
                    'string': item.get('string', item.get('text', '')),
                    'button_text': item.get('button_text', '无注释'),
                    'delay': item.get('delay', 1000),
                    'order': item.get('order', str(i))
                }
                writer.writerow(csv_item)
        
        print(f"✓ 转换成功!")
        print(f"  输入: {json_file}")
        print(f"  输出: {csv_file}")
        return True
        
    except FileNotFoundError:
        print(f"✗ 错误: 文件不存在 - {json_file}")
        return False
    except json.JSONDecodeError as e:
        print(f"✗ 错误: JSON格式无效 - {e}")
        return False
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def batch_convert(directory='.'):
    """
    批量转换目录下的所有JSON文件
    
    参数:
        directory: 要搜索的目录(默认为当前目录)
    """
    print(f"在目录 '{directory}' 中搜索JSON文件...")
    
    json_files = []
    for file in os.listdir(directory):
        if file.endswith('.json') and not file.startswith('.'):
            json_files.append(os.path.join(directory, file))
    
    if not json_files:
        print("未找到JSON文件")
        return
    
    print(f"找到 {len(json_files)} 个JSON文件\n")
    
    success_count = 0
    for json_file in json_files:
        print(f"\n处理: {json_file}")
        if convert_json_to_csv(json_file):
            success_count += 1
    
    print(f"\n{'='*50}")
    print(f"批量转换完成!")
    print(f"成功: {success_count}/{len(json_files)}")


def main():
    """主函数"""
    print("="*50)
    print("JSON到CSV转换工具")
    print("将多字符发送配置从JSON格式转换为CSV格式")
    print("="*50)
    print()
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  1. 转换单个文件:")
        print("     python convert_json_to_csv.py <json文件> [csv文件]")
        print()
        print("  2. 批量转换当前目录:")
        print("     python convert_json_to_csv.py --batch [目录]")
        print()
        print("示例:")
        print("  python convert_json_to_csv.py 11.json")
        print("  python convert_json_to_csv.py 11.json output.csv")
        print("  python convert_json_to_csv.py --batch")
        print("  python convert_json_to_csv.py --batch ./configs")
        sys.exit(1)
    
    if sys.argv[1] == '--batch':
        directory = sys.argv[2] if len(sys.argv) > 2 else '.'
        batch_convert(directory)
    else:
        json_file = sys.argv[1]
        csv_file = sys.argv[2] if len(sys.argv) > 2 else None
        convert_json_to_csv(json_file, csv_file)


if __name__ == '__main__':
    main()
