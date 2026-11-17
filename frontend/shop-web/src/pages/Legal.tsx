import { Link } from '@/lib/nav'
import { Markdown } from '@/lib/markdown'
import buyerTerms from '@/content/legal/buyer-terms.md?raw'
import privacy from '@/content/legal/privacy.md?raw'
import terms from '@/content/legal/terms.md?raw'

const DOCS = {
  terms: { source: terms, title: '이용약관' },
  'buyer-terms': { source: buyerTerms, title: '구매자 이용약관' },
  privacy: { source: privacy, title: '개인정보처리방침' },
} as const

/** 이용약관·구매자 이용약관·개인정보처리방침. 본문은 `src/content/legal/*.md`이고, 확정 전 항목이 남아 있어 그 사실을 맨 위에 알린다. */
export default function Legal({ kind }: { kind: keyof typeof DOCS }) {
  const doc = DOCS[kind]
  return (
    <article className="mx-auto max-w-3xl">
      <nav aria-label="약관 문서" className="mb-4 flex gap-3 text-sm">
        {(Object.keys(DOCS) as (keyof typeof DOCS)[]).map((k) => (
          <Link
            key={k}
            href={`/${k}`}
            aria-current={k === kind ? 'page' : undefined}
            className={k === kind ? 'font-bold text-brand-600 underline' : 'text-ink-500 hover:text-ink-700'}
          >
            {DOCS[k].title}
          </Link>
        ))}
      </nav>
      <p role="note" className="mb-6 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-ink-700">
        캣북스는 시연용 가상 서비스입니다. [확인 필요]로 표시한 항목은 확인 뒤 바뀔 수 있습니다.
      </p>
      <Markdown source={doc.source} />
    </article>
  )
}
