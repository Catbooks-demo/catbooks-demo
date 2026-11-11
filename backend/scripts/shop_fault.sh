#!/usr/bin/env bash
# 고객사 쇼핑몰(캣북스)에 결제 장애를 넣고 뺀다. 실제 모드(run_all.sh)가 떠 있을 때 운영자가 쓴다.
# AI 대화방에는 장애 버튼이 없다. 장애는 사람이 요청하면 운영자가 이 명령으로 넣는다.
#   SHOP_ADMIN_TOKEN=<관리자 토큰> bash backend/scripts/shop_fault.sh failure    # 결제가 서버 오류(5xx)로 실패
#   SHOP_ADMIN_TOKEN=<관리자 토큰> bash backend/scripts/shop_fault.sh latency    # 결제 응답 2초 지연
#   SHOP_ADMIN_TOKEN=<관리자 토큰> bash backend/scripts/shop_fault.sh clear      # 해제
#   SHOP_ADMIN_TOKEN=<관리자 토큰> bash backend/scripts/shop_fault.sh            # 지금 상태
# 관리자 토큰은 환경 변수 SHOP_ADMIN_TOKEN으로 받는다(출력하지 않는다). 판매 서버 주소는 SHOP_API_URL.
set -eu
URL="${SHOP_API_URL:-http://127.0.0.1:8010}/api/shop/admin/faults"
TOKEN="${SHOP_ADMIN_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  echo "SHOP_ADMIN_TOKEN 이 비어 있습니다. 판매 서버 관리자 토큰을 넣고 다시 실행하세요." >&2
  exit 1
fi
AUTH="Authorization: Bearer $TOKEN"
case "${1:-status}" in
  failure) curl -s -X POST "$URL" -H "$AUTH" -H "Content-Type: application/json" -d '{"kind":"failure"}' ;;
  latency) curl -s -X POST "$URL" -H "$AUTH" -H "Content-Type: application/json" -d '{"kind":"latency","delay_ms":2000}' ;;
  clear) curl -s -X DELETE "$URL" -H "$AUTH" ;;
  status) curl -s "$URL" -H "$AUTH" ;;
  *) echo "사용법: shop_fault.sh [failure|latency|clear|status]" >&2; exit 2 ;;
esac
echo
