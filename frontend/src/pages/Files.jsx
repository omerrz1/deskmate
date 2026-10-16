import { useSearchParams } from 'react-router-dom'
import { usePoll } from '../api'
import { useAuth } from '../Auth'
import FileManager from '../FileManager'
import { PageHeader, Skeleton, useTitle } from '../ui'

/** Every place a person can look for files, in one explorer: their own files, the company drive, each project's drive, and (for admins) each AI employee's computer. */
export default function Files() {
  useTitle('Files')
  const { isAdmin } = useAuth()
  const [places] = usePoll('/drives', 0)
  const [params] = useSearchParams()
  if (!places) return <div className="page wide"><Skeleton rows={5} /></div>
  const wanted = params.get('drive')
  const owner = places.some((p) => p.owner === wanted) ? wanted : places[0].owner
  return (
    <div className="page wide files-page">
      <PageHeader title="Files"
        sub={isAdmin ? 'Your files, the company drive, every project and every AI employee’s computer. Orange folders are what the team leaves for the admins.' : 'Your own files, and the drives of the projects you work on.'} />
      <div className="files-box"><FileManager places={places} owner={owner} fill /></div>
    </div>
  )
}
