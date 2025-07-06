from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
import json
import time
import argparse

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# 缓存解析结果
gacha_cache = {"data": None, "timestamp": None}

def parse_gacha_table(table):
    """解析单个卡池表格"""
    # 提取卡池名称
    header = table.find('th', class_='ys-qy-title')
    name = "未知卡池"
    if header:
        name_img = header.find('img')
        if name_img and name_img.get('alt'):
            name = name_img['alt']
        else:
            name = header.text.strip()
    
    # 确定卡池类型
    pool_type = "角色池"
    if "武器" in name: 
        pool_type = "武器池"
    elif "集录" in name: 
        pool_type = "混池（集录）"
    
    # 查找所有行
    rows = table.find_all('tr')
    data = {
        "name": name,
        "type": pool_type,
        "version": "",
        "version_key": "其他",
        "start_time": "",
        "end_time": "",
        "five_stars": [],
        "four_stars": []
    }
    
    # 处理所有行
    for row in rows:
        th = row.find('th')
        if not th:
            continue
            
        header_text = th.get_text(strip=True)
        td = row.find('td')
        if not td:
            continue
        
        # 处理时间
        if "时间" in header_text or "期間" in header_text:
            date_str = td.get_text(strip=True)
            if "~" in date_str:
                parts = date_str.split('~', 1)
                if len(parts) == 2:
                    data["start_time"] = parts[0].strip()
                    data["end_time"] = parts[1].strip()
            elif "至" in date_str:
                parts = date_str.split('至', 1)
                if len(parts) == 2:
                    data["start_time"] = parts[0].strip()
                    data["end_time"] = parts[1].strip()
        
        # 处理版本
        elif "版本" in header_text:
            data["version"] = td.get_text(strip=True)
            # 提取版本号
            version_match = re.search(r'(\d+\.\d+)(上半|下半)?', data["version"])
            if version_match:
                data["version_key"] = version_match.group(1)
        
        # 处理五星内容
        elif "5星" in header_text or "五星" in header_text:
            data["five_stars"] = [a.get_text(strip=True) for a in td.find_all('a')]
        
        # 处理四星内容
        elif "4星" in header_text or "四星" in header_text:
            data["four_stars"] = [a.get_text(strip=True) for a in td.find_all('a')]
    
    # 对于集录祈愿，如果没有明确的五星行，尝试从其他部分提取
    if pool_type == "混池（集录）" and not data["five_stars"]:
        # 尝试从角色部分提取五星
        character_section = table.find('th', string=re.compile(r'角色|人物'))
        if character_section:
            char_td = character_section.find_next('td')
            if char_td:
                # 假设前8个角色是五星
                characters = [a.get_text(strip=True) for a in char_td.find_all('a')]
                data["five_stars"].extend(characters[:10])
        
        # 尝试从武器部分提取五星
        weapon_section = table.find('th', string=re.compile(r'武器'))
        if weapon_section:
            weapon_td = weapon_section.find_next('td')
            if weapon_td:
                # 假设前8个武器是五星
                weapons = [a.get_text(strip=True) for a in weapon_td.find_all('a')]
                data["five_stars"].extend(weapons[:18])
    
    return data

def fetch_gacha_data():
    """获取祈愿数据（核心逻辑）"""
    try:
        print("开始从biligame获取祈愿数据...")
        
        # 解析往期祈愿和集录祈愿页面
        print("获取往期祈愿页面...")
        soup1 = BeautifulSoup(requests.get("https://wiki.biligame.com/ys/往期祈愿", timeout=15).content, 'html.parser')
        print("获取集录祈愿页面...")
        soup2 = BeautifulSoup(requests.get("https://wiki.biligame.com/ys/集录祈愿", timeout=15).content, 'html.parser')
        
        all_gacha_data = []
        seen_names = set()
        current_year = datetime.now().year
        
        print(f"发现往期祈愿表格: {len(soup1.find_all('table', class_='wikitable'))} 个")
        print(f"发现集录祈愿表格: {len(soup2.find_all('table', class_='wikitable'))} 个")
        
        # 解析所有卡池表格
        tables = [*soup1.find_all('table', class_='wikitable'), *soup2.find_all('table', class_='wikitable')]
        print(f"总表格数: {len(tables)}")
        
        for i, table in enumerate(tables, 1):
            try:
                print(f"解析表格 {i}/{len(tables)}...")
                entry = parse_gacha_table(table)
                if not entry or not entry.get("name"):
                    print(f"表格 {i} 未找到有效名称，跳过")
                    continue
                    
                if entry["name"] in seen_names:
                    print(f"跳过重复卡池: {entry['name']}")
                    continue
                    
                # 添加年份到日期
                if entry["start_time"]:
                    entry["start_time"] = f"{current_year}/" + entry["start_time"].replace('/', '-')
                if entry["end_time"]:
                    entry["end_time"] = f"{current_year}/" + entry["end_time"].replace('/', '-')
                
                print(f"添加卡池: {entry['name']} ({entry['type']})")
                all_gacha_data.append(entry)
                seen_names.add(entry["name"])
            except Exception as e:
                print(f"解析表格 {i} 出错: {e}")
                continue

        print(f"成功解析卡池数: {len(all_gacha_data)}")
        
        # 按版本分组
        version_data = {}
        for entry in all_gacha_data:
            key = entry.get("version_key", "其他")
            version_data.setdefault(key, []).append(entry)
        
        # 获取最新两个版本
        sorted_versions = sorted(
            version_data.keys(), 
            key=lambda v: [int(part) for part in v.split('.')] if v != "其他" else [0, 0], 
            reverse=True
        )[:2]
        
        print(f"最新两个版本: {sorted_versions}")
        
        # 构建最终数据结构
        result = {
            "last_updated": datetime.now().isoformat(),
            "gacha_data": [entry for version in sorted_versions for entry in version_data[version]]
        }
        
        return result
    
    except Exception as e:
        print(f"获取祈愿数据出错: {e}")
        return {"error": f"无法获取祈愿数据: {str(e)}"}

@app.route('/gacha', methods=['GET'])
def get_gacha_data():
    # 检查缓存有效性（5分钟）
    if gacha_cache["data"] and gacha_cache["timestamp"]:
        if (datetime.now() - gacha_cache["timestamp"]).seconds < 300:
            return jsonify(gacha_cache["data"])
    
    try:
        result = fetch_gacha_data()
        # 更新缓存
        gacha_cache["data"] = result
        gacha_cache["timestamp"] = datetime.now()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='原神祈愿数据抓取服务')
    parser.add_argument('--save-json', type=str, help='保存数据到指定JSON文件', metavar='FILE')
    args = parser.parse_args()

    if args.save_json:
        print(f"运行模式: 保存数据到文件 {args.save_json}")
        start_time = time.time()
        
        # 尝试最多3次获取数据
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                print(f"尝试 #{attempt} 获取数据...")
                result = fetch_gacha_data()
                
                if "error" in result:
                    print(f"获取数据失败: {result['error']}")
                    if attempt < max_retries:
                        retry_delay = 5
                        print(f"{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        print("所有尝试均失败，保存错误信息")
                        result = {"error": "所有尝试均失败: " + result.get("error", "未知错误")}
                
                # 保存到文件
                with open(args.save_json, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                
                elapsed = time.time() - start_time
                print(f"数据已保存至 {args.save_json}, 耗时: {elapsed:.2f}秒")
                break
                
            except Exception as e:
                print(f"保存数据时出错: {str(e)}")
                if attempt < max_retries:
                    retry_delay = 5
                    print(f"{retry_delay}秒后重试...")
                    time.sleep(retry_delay)
                else:
                    print("所有尝试均失败")
                    with open(args.save_json, 'w', encoding='utf-8') as f:
                        json.dump({"error": f"所有尝试均失败: {str(e)}"}, f, ensure_ascii=False, indent=2)
    else:
        print("运行模式: 启动Flask服务")
        app.run(host='0.0.0.0', port=5000, debug=False)
