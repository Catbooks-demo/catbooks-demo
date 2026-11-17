import { TOSS_AGREEMENT_ID, TOSS_METHODS_ID, type TossWidget } from "@/lib/toss";

/** 결제위젯 자리(결제 방법 + 약관). 토스가 이 두 칸 안에 그린다. */
export function TossWidgetBox({ widget, title = "결제 수단" }: { widget: TossWidget; title?: string }) {
  return (
    <section className="rounded-xl border border-ink-100 bg-surface p-5" aria-busy={widget.status === "loading"}>
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-bold">{title}</h2>
        <span className="chip bg-amber-50 text-amber-700">테스트 결제</span>
      </div>
      {widget.status === "idle" && <p className="text-xs text-ink-500">결제 금액을 확인하면 결제 방법이 나옵니다.</p>}
      {widget.status === "loading" && <p className="text-xs text-ink-500">결제창을 불러오는 중…</p>}
      {widget.status === "error" && (
        <p role="alert" className="text-xs text-red-700">결제창을 불러오지 못했습니다. 새로고침해 주세요. ({widget.error})</p>
      )}
      {/* 토스 결제위젯은 자기 배경색으로 그리므로 둥근 상자로 감싼다 */}
      <div className="-mx-2 overflow-hidden rounded-lg">
        <div id={TOSS_METHODS_ID} />
        <div id={TOSS_AGREEMENT_ID} />
      </div>
      <p className="mt-2 text-[11px] text-ink-500">토스페이먼츠 테스트 결제입니다. 실제로 돈이 빠져나가지 않습니다.</p>
    </section>
  );
}
