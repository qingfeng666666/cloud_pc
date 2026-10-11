"""Tell the running same-device host to connect an already configured slave."""
import argparse
import json
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="手动连接已配置的云电脑从机")
    parser.add_argument("target", help="从机IP、IP:端口或主机记录中的从机ID")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:19527"
    try:
        with opener.open(base + "/api/slaves", timeout=5) as response:
            devices = json.load(response)["slaves"]
        matches = [device for device in devices if args.target in
                   {device["id"], device["address"], device["address"].rsplit(":", 1)[0]}]
        if len(matches) != 1:
            parser.error("目标未配置或不唯一；请使用完整IP:端口或ID")
        request = urllib.request.Request(base + "/api/" + matches[0]["id"] + "/connect",
                                         data=b"{}", headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=5) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except (OSError, ValueError, KeyError) as exc:
        print("手动连接失败：" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
