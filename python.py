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
    try:
        # 提取卡池名称
        header = table.find('th', colspan="2")
        name = "未知卡池"
        if header:
            name_img = header.find('img')
            if name_img and name_img.get('alt'):
                name = name_img['alt']
            else:
                name_text = header.get_text(strip=True)
                if name_text:
                    name = name_text
        
        # 确定卡池类型
        pool_type = "角色池"
        if "武器" in name or "神铸赋形" in name: 
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
                version_match = re.search(r'(\d+\.\d+|[月之]\S+)(上半|下半)?', data["version"])
                if version_match:
                    data["version_key"] = version_match.group(1)
            
            # 处理五星内容
            elif "5星" in header_text or "五星" in header_text or "5星角色" in header_text or "5星武器" in header_text:
                data["five_stars"] = [a.get_text(strip=True) for a in td.find_all('a') if a.get_text(strip=True)]
            
            # 处理四星内容
            elif "4星" in header_text or "四星" in header_text or "4星角色" in header_text or "4星武器" in header_text:
                data["four_stars"] = [a.get_text(strip=True) for a in td.find_all('a') if a.get_text(strip=True)]
        
        return data
    except Exception as e:
        print(f"解析单个表格时出错: {e}")
        return None

def fetch_gacha_data():
    """获取祈愿数据（核心逻辑）"""
    try:
        print("开始从biligame获取祈愿数据...")
        
        # 解析往期祈愿和集录祈愿页面
        print("获取往期祈愿页面...")
        response1 = requests.get("https://wiki.biligame.com/ys/往期祈愿", timeout=30)
        response1.raise_for_status()
        soup1 = BeautifulSoup(response1.content, 'html.parser')
        
        print("获取集录祈愿页面...")
        response2 = requests.get("https://wiki.biligame.com/ys/集录祈愿", timeout=30)
        response2.raise_for_status()
        soup2 = BeautifulSoup(response2.content, 'html.parser')
        
        all_gacha_data = []
        seen_names = set()
        current_year = datetime.now().year
        
        # 修改点：正确提取嵌套表格
        tables = []
        
        # 处理往期祈愿页面：查找所有包含卡池的内部表格
        for outer_table in soup1.find_all('table', class_='wikitable'):
            # 在内层查找所有卡池表格
            inner_tables = outer_table.find_all('table', class_='ys-qy-table')
            tables.extend(inner_tables)
        
        # 处理集录祈愿页面：直接获取所有表格
        tables.extend(soup2.find_all('table', class_='wikitable'))
        
        print(f"发现有效卡池表格: {len(tables)} 个")
        
        # 解析所有卡池表格
        successful_parses = 0
        for i, table in enumerate(tables, 1):
            try:
                print(f"解析表格 {i}/{len(tables)}...")
                entry = parse_gacha_table(table)
                if not entry or not entry.get("name") or entry["name"] == "未知卡池":
                    print(f"表格 {i} 未找到有效名称，跳过")
                    continue
                    
                if entry["name"] in seen_names:
                    print(f"跳过重复卡池: {entry['name']}")
                    continue
                    
                # 添加年份到日期（如果日期中还没有年份）
                if entry["start_time"] and not re.search(r'\d{4}', entry["start_time"]):
                    entry["start_time"] = f"{current_year}/" + entry["start_time"].replace('/', '-')
                if entry["end_time"] and not re.search(r'\d{4}', entry["end_time"]):
                    entry["end_time"] = f"{current_year}/" + entry["end_time"].replace('/', '-')
                
                print(f"添加卡池: {entry['name']} ({entry['type']}) - 五星: {len(entry['five_stars'])}个, 四星: {len(entry['four_stars'])}个")
                all_gacha_data.append(entry)
                seen_names.add(entry["name"])
                successful_parses += 1
                
            except Exception as e:
                print(f"解析表格 {i} 出错: {e}")
                continue

        print(f"成功解析卡池数: {successful_parses}")
        
        if successful_parses == 0:
            return {"error": "未能成功解析任何卡池数据"}
        
        # 按版本分组
        version_data = {}
        for entry in all_gacha_data:
            key = entry.get("version_key", "其他")
            if not key or key == "其他":
                # 尝试从名称中提取版本信息
                name = entry["name"]
                if "089" in name:
                    key = "月之一"
                elif "088" in name:
                    key = "月之一"
                elif "087" in name:
                    key = "5.8"
                elif "086" in name:
                    key = "5.8"
                elif "085" in name:
                    key = "5.7"
                elif "084" in name:
                    key = "5.7"
                elif "083" in name:
                    key = "5.6"
                elif "082" in name:
                    key = "5.6"
                elif "081" in name:
                    key = "5.5"
                elif "080" in name:
                    key = "5.5"
                elif "079" in name:
                    key = "5.4"
                elif "078" in name:
                    key = "5.4"
                elif "077" in name:
                    key = "5.3"
                elif "076" in name:
                    key = "5.3"
                elif "075" in name:
                    key = "5.2"
                elif "074" in name:
                    key = "5.2"
                else:
                    key = "其他"
            
            version_data.setdefault(key, []).append(entry)
        
        # 获取最新两个版本
        def version_sort_key(v):
            if v == "其他":
                return [0, 0]
            elif v.startswith("月之"):
                try:
                    # 月之版本排在前面，数字越大越新
                    version_num = int(v.replace("月之", ""))
                    return [999, version_num]
                except:
                    return [999, 0]
            else:
                try:
                    return [int(part) for part in v.split('.')]
                except:
                    return [0, 0]
        
        sorted_versions = sorted(
            version_data.keys(), 
            key=version_sort_key, 
            reverse=True
        )
        
        # 只取最新的2个版本
        latest_versions = sorted_versions[:2]
        print(f"所有版本: {sorted_versions}")
        print(f"最新两个版本: {latest_versions}")
        
        # 构建最终数据结构
        result = {
            "last_updated": datetime.now().isoformat(),
            "total_pools": successful_parses,
            "latest_versions": latest_versions,
            "gacha_data": []
        }
        
        # 只包含最新两个版本的数据
        for version in latest_versions:
            result["gacha_data"].extend(version_data[version])
        
        print(f"最终返回卡池数: {len(result['gacha_data'])}")
        return result
    
    except requests.RequestException as e:
        print(f"网络请求出错: {e}")
        return {"error": f"网络请求失败: {str(e)}"}
    except Exception as e:
        print(f"获取祈愿数据出错: {e}")
        import traceback
        traceback.print_exc()
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
        print(f"API返回时出错: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='原神祈愿数据抓取服务')
    parser.add_argument('--save-json', type=str, help='保存数据到指定JSON文件', metavar='FILE')
    parser.add_argument('--limit-versions', type=int, default=5, help='限制返回的版本数量，默认2个')
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
