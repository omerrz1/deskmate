import { usePoll } from '../api'
import { Mail } from '../Mail'
import { PageHeader, useTitle } from '../ui'

/** The admin's own mail: messages from the team, and outside emails and texts waiting for approval. */
export default function Inbox() {
  useTitle('Inbox')
  const [team] = usePoll('/agents', 0)
  return (
    <div className="page wide inbox-page">
      <PageHeader title="Inbox" sub="Messages from your team, and emails and texts waiting for your approval." />
      <Mail team={team || []} comfy />
    </div>
  )
}
