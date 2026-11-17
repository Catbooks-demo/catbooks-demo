// 약관·방침 본문(마크다운)을 그리는 작은 변환기. 제목·문단·목록·표·굵은 글씨·구분선만 지원한다.
// 외부 라이브러리 없이 텍스트 노드로만 만들기 때문에 HTML이 섞여 있어도 그대로 글자로 보인다.
import type { ReactNode } from 'react'

function inline(text: string, keyBase: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
      <strong key={`${keyBase}-${i}`}>{part.slice(2, -2)}</strong>
    ) : (
      part
    ),
  )
}

const isTableLine = (line: string) => line.trimStart().startsWith('|')
const isSeparator = (line: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-')
const splitRow = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const out: ReactNode[] = []
  let i = 0
  let key = 0
  const next = () => `b${key++}`

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }

    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      const level = heading[1].length
      const k = next()
      const body = inline(heading[2], k)
      if (level === 1) out.push(<h1 key={k} className="mb-4 text-2xl font-bold text-ink-900">{body}</h1>)
      else if (level === 2) out.push(<h2 key={k} className="mb-2 mt-8 text-lg font-bold text-ink-900">{body}</h2>)
      else out.push(<h3 key={k} className="mb-1 mt-5 text-base font-bold text-ink-800">{body}</h3>)
      i++
      continue
    }

    if (/^\s*---+\s*$/.test(line)) {
      out.push(<hr key={next()} className="my-6 border-ink-100" />)
      i++
      continue
    }

    if (isTableLine(line)) {
      const rows: string[][] = []
      while (i < lines.length && isTableLine(lines[i])) {
        if (!isSeparator(lines[i])) rows.push(splitRow(lines[i]))
        i++
      }
      const [head, ...body] = rows
      const k = next()
      out.push(
        <div key={k} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {head.map((cell, c) => (
                  <th key={c} className="border border-ink-100 bg-surface-2 px-3 py-2 text-left font-semibold">
                    {inline(cell, `${k}h${c}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border border-ink-100 px-3 py-2 align-top">
                      {inline(cell, `${k}r${r}c${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const ordered = /^\s*\d+\.\s+/.test(line)
    if (ordered || /^\s*-\s+/.test(line)) {
      const pattern = ordered ? /^\s*\d+\.\s+(.*)$/ : /^\s*-\s+(.*)$/
      const items: string[] = []
      while (i < lines.length && pattern.test(lines[i])) {
        items.push(pattern.exec(lines[i])![1])
        i++
      }
      const k = next()
      const children = items.map((item, n) => <li key={n}>{inline(item, `${k}-${n}`)}</li>)
      out.push(
        ordered ? (
          <ol key={k} className="my-2 list-decimal space-y-1 pl-6 text-sm leading-relaxed">{children}</ol>
        ) : (
          <ul key={k} className="my-2 list-disc space-y-1 pl-6 text-sm leading-relaxed">{children}</ul>
        ),
      )
      continue
    }

    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^#{1,3}\s/.test(lines[i]) &&
      !isTableLine(lines[i]) &&
      !/^\s*---+\s*$/.test(lines[i])
    ) {
      para.push(lines[i].trim())
      i++
    }
    const k = next()
    out.push(
      <p key={k} className="my-2 text-sm leading-relaxed text-ink-700">
        {inline(para.join(' '), k)}
      </p>,
    )
  }

  return <>{out}</>
}
