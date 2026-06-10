import argparse
import json
import logging

from src.services.vk_delivery import log_vk_runtime_config, send_vk_message


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a test VK message through the configured group token.")
    parser.add_argument("--vk-user-id", required=True, help="Numeric VK user id that allowed group messages.")
    parser.add_argument("--message", default="Тестовое сообщение Shamrai VK delivery.", help="Message text.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log_vk_runtime_config()
    result = send_vk_message(vk_user_id=args.vk_user_id, message=args.message)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
