import requests
import sys

def test_api_key(api_key):
    print(f"Testing API Key: {api_key}")
    url = f"https://api.themoviedb.org/3/authentication/token/new?api_key={api_key}"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            print("\n✅ API Key 有效！")
            print("连接 TMDB 成功。")
            
            # Try a search
            search_url = f"https://api.themoviedb.org/3/search/movie?api_key={api_key}&query=Inception"
            search_res = requests.get(search_url).json()
            if search_res.get('results'):
                first = search_res['results'][0]
                print(f"\n测试搜索 'Inception':")
                print(f"标题: {first['title']}")
                print(f"ID: {first['id']}")
            else:
                print("\n⚠️ API 连接成功，但搜索无结果。")
                
        elif response.status_code == 401:
            print("\n❌ API Key 无效或被拒绝。请检查是否复制完整。")
            print(f"错误信息: {response.json().get('status_message')}")
        else:
            print(f"\n❌ 连接失败，状态码: {response.status_code}")
            
    except Exception as e:
        print(f"\n❌ 网络连接错误: {e}")
        print("请检查你的网络连接（TMDB 可能需要代理）。")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用方法: python test_tmdb.py <YOUR_API_KEY>")
    else:
        test_api_key(sys.argv[1])
