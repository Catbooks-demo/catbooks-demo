# 게이트웨이 이미지: 쇼핑몰 화면(shop-web)을 빌드해 정적 파일로 담고 nginx 하나가 한 포트로 서빙한다.
# 빌드 컨텍스트는 frontend/: docker build -f nginx/Dockerfile frontend
# (공용 UI 패키지가 저장소 주소 의존성이라 소스 관리 도구가 들어 있는 Debian 기반 이미지를 쓴다)
FROM node:22-bookworm AS build
WORKDIR /app

COPY package.json package-lock.json ./
COPY shop-web/package.json shop-web/
RUN npm ci --no-audit --no-fund

COPY . .
# 게이트웨이 경로 기준 값. 같은 출처의 상대 경로라 서버 PC 주소가 바뀌어도 다시 빌드하지 않는다.
ARG VITE_SHOP_API_URL=/shop-api
# 쇼핑몰 상단 '상담원 콘솔' 링크(AXcellent 화면 주소). 비우면 링크 기본값(localhost)을 쓴다
ARG VITE_DASHBOARD_WEB_URL=
# 토스페이먼츠 결제위젯 클라이언트 키(테스트 test_gck_…, 공개 키). 비우면 쇼핑몰은 데모 결제로 동작한다. 시크릿 키는 넣지 않는다
ARG VITE_TOSS_CLIENT_KEY=
ENV VITE_SHOP_API_URL=$VITE_SHOP_API_URL \
    VITE_DASHBOARD_WEB_URL=$VITE_DASHBOARD_WEB_URL \
    VITE_TOSS_CLIENT_KEY=$VITE_TOSS_CLIENT_KEY
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/shop-web/dist /srv/shop
# gateway.conf는 템플릿이다: 공식 이미지가 시작할 때 /etc/nginx/templates/*.template을 envsubst로 채워 conf.d/에 쓴다.
# 필터로 SHOP_API_UPSTREAM만 바꾼다($host·$uri 같은 nginx 변수는 그대로 둔다)
ENV SHOP_API_UPSTREAM=http://shop-api:8010 \
    NGINX_ENVSUBST_FILTER=^SHOP_API_UPSTREAM$
COPY nginx/gateway.conf /etc/nginx/templates/default.conf.template
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
