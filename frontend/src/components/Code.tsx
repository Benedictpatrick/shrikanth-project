import { useMemo } from 'react'

const PY_KW = new Set(
  'and as assert break class continue def del elif else except finally for from global if import in is lambda not or pass raise return try while with yield True False None self'.split(' '),
)
const CB_KW = new Set(
  'IDENTIFICATION ENVIRONMENT DATA PROCEDURE DIVISION SECTION PROGRAM-ID INPUT-OUTPUT FILE-CONTROL FILE WORKING-STORAGE LINKAGE SELECT ASSIGN TO ORGANIZATION IS LINE SEQUENTIAL STATUS FD PIC PICTURE VALUE OCCURS TIMES REDEFINES COMP COMP-3 USAGE DISPLAY MOVE ADD SUBTRACT MULTIPLY DIVIDE COMPUTE ROUNDED IF ELSE END-IF EVALUATE WHEN OTHER END-EVALUATE PERFORM UNTIL VARYING FROM BY END-PERFORM THRU OPEN CLOSE READ WRITE AT END END-READ INTO NOT STOP RUN GOBACK GIVING INITIALIZE SET TRUE FALSE AND OR COPY STRING INSPECT CONVERTING DELIMITED SIZE SPACE SPACES ZERO ZEROS ZEROES INVALID CALL USING'.split(' '),
)

type Tok = { t: 'kw' | 'str' | 'num' | 'com' | 'txt'; s: string }

function tokenize(line: string, lang: 'py' | 'cobol'): Tok[] {
  if (lang === 'cobol' && line.length > 6 && (line[6] === '*' || line[6] === '/')) return [{ t: 'com', s: line }]
  const out: Tok[] = []
  const kws = lang === 'py' ? PY_KW : CB_KW
  const re = /('(?:[^']|'')*'|"(?:[^"\\]|\\.)*")|(#.*$)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_-]*)|(\s+|.)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(line))) {
    if (m[1]) out.push({ t: 'str', s: m[1] })
    else if (m[2] && lang === 'py') out.push({ t: 'com', s: m[2] })
    else if (m[3]) out.push({ t: 'num', s: m[3] })
    else if (m[4]) out.push({ t: kws.has(lang === 'py' ? m[4] : m[4].toUpperCase()) ? 'kw' : 'txt', s: m[4] })
    else out.push({ t: 'txt', s: m[0] })
  }
  return out
}

export function Code({
  text,
  lang,
  highlight,
  maxHeight,
}: {
  text: string
  lang: 'py' | 'cobol' | 'plain'
  highlight?: number[]
  maxHeight?: number
}) {
  const lines = useMemo(() => text.replace(/\r\n/g, '\n').split('\n'), [text])
  const rows = useMemo(
    () => lines.map((l) => (lang === 'plain' ? [{ t: 'txt', s: l } as Tok] : tokenize(l, lang))),
    [lines, lang],
  )
  const hl = new Set(highlight ?? [])
  return (
    <div className="code" style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <tbody>
          {rows.map((toks, i) => (
            <tr key={i} className={hl.has(i + 1) ? 'hl' : undefined}>
              <td className="ln">{i + 1}</td>
              <td>
                {toks.map((tk, j) =>
                  tk.t === 'txt' ? tk.s : (
                    <span key={j} className={`tk-${tk.t}`}>
                      {tk.s}
                    </span>
                  ),
                )}
                {toks.length === 0 ? ' ' : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Diff({ lines }: { lines: string[] }) {
  return (
    <div className="diff">
      {lines.map((l, i) => {
        const cls = l.startsWith('+++') || l.startsWith('---') || l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : ''
        return (
          <div key={i} className={cls}>
            {l || ' '}
          </div>
        )
      })}
    </div>
  )
}
