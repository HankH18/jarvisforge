import type { TranscriptLine } from '../hooks/useJarvisSession'

export function TranscriptView(props: { lines: TranscriptLine[] }) {
  const { lines } = props

  if (lines.length === 0) {
    return (
      <div className="transcript" data-testid="transcript" role="log" aria-label="Transcript">
        <p className="transcript__empty" data-testid="transcript-empty">
          Nothing said yet — join and start talking.
        </p>
      </div>
    )
  }

  return (
    <div className="transcript" data-testid="transcript" role="log" aria-label="Transcript">
      <ol className="transcript__list">
        {lines.map((line) => (
          <li
            key={line.id}
            className="transcript__line"
            data-testid="transcript-line"
            data-role={line.role}
            data-final={line.final ? 'true' : 'false'}
          >
            {line.text}
          </li>
        ))}
      </ol>
    </div>
  )
}
