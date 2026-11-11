// 루트 .env (VITE_*) 값. 없으면 로컬 기본 포트를 쓴다.
export const SHOP_API_URL = import.meta.env.VITE_SHOP_API_URL ?? 'http://localhost:8010'
/** 상단 '상담원 콘솔' 링크가 여는 dashboard-web 주소 */
export const DASHBOARD_WEB_URL = import.meta.env.VITE_DASHBOARD_WEB_URL ?? 'http://localhost:3001'
/**
 * 토스페이먼츠 결제위젯 클라이언트 키(테스트 `test_gck_…`만). 브라우저에 그대로 실리는 공개 키다.
 * 시크릿 키는 화면에 두지 않는다(shop-api만 가진다). 비었거나 테스트 위젯 키가 아니면 데모 결제로 동작한다.
 */
const tossKey = (import.meta.env.VITE_TOSS_CLIENT_KEY ?? '').trim()
export const TOSS_CLIENT_KEY = tossKey.startsWith('test_gck_') ? tossKey : ''
