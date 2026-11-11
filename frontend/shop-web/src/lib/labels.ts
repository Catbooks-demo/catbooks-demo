export const CATEGORY_KO: Record<string, string> = {
  refund: "환불·반품", cancel: "주문 취소", delivery: "배송", defect: "불량·파손·오배송", payment: "결제",
  product: "상품", review: "후기", account: "적립금·계정", other: "기타",
};
export const SENTIMENT_KO: Record<string, string> = { calm: "차분", frustrated: "답답함", angry: "화남" };
export const URGENCY_KO: Record<string, string> = { low: "낮음", medium: "보통", high: "높음" };
export const STYLE_KO: Record<string, string> = { empathetic: "공감형", concise: "간결형", step_by_step: "단계형" };
export const STATUS_KO: Record<string, string> = { open: "진행 중", resolved: "해결", escalated: "이관" };
export const URGENCY_DOT: Record<string, string> = { high: "bg-red-500", medium: "bg-amber-400", low: "bg-emerald-400" };

export const CLAIM_TYPE_KO: Record<string, string> = { cancel: "주문 취소", return: "반품", exchange: "교환", compensation: "보상" };
export const CLAIM_REASON_KO: Record<string, string> = {
  change_of_mind: "단순변심", defect: "상품 불량", wrong_item: "오배송", damaged: "배송 중 파손",
  late_delivery: "배송 지연", missing_item: "배송 완료인데 받지 못함", other: "기타",
};
export const CLAIM_STATUS_KO: Record<string, string> = {
  needs_info: "사진 필요", needs_review: "상담원 확인 중", awaiting_return: "반품 입고 대기", return_received: "입고 완료·처리 대기",
  completed: "처리 완료", rejected: "접수 불가", withdrawn: "철회",
};
export const CLAIM_STATUS_TONE: Record<string, string> = {
  needs_info: "bg-amber-50 text-amber-700", needs_review: "bg-violet-50 text-violet-700", awaiting_return: "bg-brand-50 text-brand-700",
  return_received: "bg-brand-50 text-brand-700", completed: "bg-emerald-50 text-emerald-700", rejected: "bg-red-50 text-red-700",
  withdrawn: "bg-ink-100 text-ink-500",
};
export const INSPECTION_KO: Record<string, string> = { pending: "검수 대기", ok: "정상", mismatch: "사유와 불일치" };
export const PAYMENT_KO: Record<string, string> = { card: "신용·체크카드", transfer: "계좌이체", easy_pay: "간편결제", phone: "휴대폰", other: "기타 결제" };

/** Rule codes in plain Korean, for the agent's rule tables. */
export const RULE_KO: Record<string, string> = {
  INVALID_REASON: "사유·종류 불일치", OWNER_MISMATCH: "주문자 불일치", ITEM_NOT_IN_ORDER: "영수증에 없는 상품", ALREADY_CLAIMED: "중복 신청", QTY_ADJUSTED: "수량 보정",
  CANCEL_TOO_LATE: "출고 후 취소", ALREADY_CANCELLED: "이미 취소됨", NOT_DELIVERED_YET: "배송 완료 전", CANCEL_INSTEAD: "취소로 신청해야 함",
  WINDOW_MIND_EXPIRED: "단순변심 기간 경과", WINDOW_DEFECT_EXPIRED: "불량 신청 기간 경과", NON_RETURNABLE: "환불 불가 상품", UNSEALED: "개봉·사용",
  EBOOK_DEFECT: "전자책 오류", NEED_EVIDENCE: "증빙 사진 필요", NOT_LATE: "지연 기준 미달", LATE_COMPENSATION: "지연 보상",
  ALREADY_COMPENSATED: "이미 보상됨", MISSING_ITEM_INVESTIGATE: "미수령 조사", RETURN_FEE: "반품 배송비", SHIPPING_FEE_LOST: "무료배송 상실",
  AMOUNT_ADJUSTED: "금액 보정", AMOUNT_EXCEEDS_RECEIPT: "영수증 금액 초과", DEFECT_COMPENSATION: "불량 보상", OVER_AUTO_LIMIT: "자동 한도 초과",
  ABUSE_SUSPECTED: "점검 대상", OTHER_REASON: "기타 사유", EXCHANGE_STOCK_LOW: "교환 재고 부족",
};
export const BLOCKER_KO: Record<string, string> = {
  NOT_INSPECTED: "검수 대기", ON_HOLD: "상담원 확인", ABUSE_HOLD: "점검 대상", OVER_LIMIT: "한도 초과", OUT_OF_STOCK: "재고 부족", LIMIT: "개수 제한",
};
export const AUDIT_KO: Record<string, string> = {
  submitted: "신청 접수", decided: "자동 판정", evidence_added: "사진 추가", withdrawn: "철회", agent_override: "상담원 승인",
  agent_confirmed: "상담원 거절", received: "반품 입고", inspected: "검수 결과 변경", paid: "지급 완료",
};
export const OUTCOME_KO: Record<string, string> = {
  auto_rejected: "자동 거절", needs_info: "자료 요청", needs_review: "상담원 확인", approved_pending_return: "승인(입고 후 지급)", auto_approved: "자동 승인·지급",
};

// shop-api 주문 상태 코드.
export const ORDER_STATUS_KO: Record<string, string> = {
  pending_payment: "결제 대기", payment_failed: "결제 실패", paid: "결제 완료", cancelled: "취소됨",
};
export const ORDER_STATUS_TONE: Record<string, string> = {
  pending_payment: "bg-amber-50 text-amber-700", payment_failed: "bg-red-50 text-red-700", paid: "bg-emerald-50 text-emerald-700", cancelled: "bg-ink-100 text-ink-500",
};
