import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import Avatar from '../Avatar'
import Icon from '../Icon'
import APPS from './apps.json'  // counts, categories and logos; made by backend/scripts/export_site_apps.py

/* ---------- shared pieces ---------- */

/** The main call to action: create a workspace, or sign in when sign-up is closed, or open the workspace when already signed in. */
function Primary({ st, children, className = '' }) {
  if (st?.authenticated) return <Link className={`s-btn solid big ${className}`} to="/">Open your workspace <Icon name="arrow" size={16} /></Link>
  if (st && (st.signup_public ?? st.signup_open) === false) return <Link className={`s-btn solid big ${className}`} to="/signin">Sign in</Link>
  return <Link className={`s-btn solid big ${className}`} to="/signup">{children || 'Create your workspace'} <Icon name="arrow" size={16} /></Link>
}

function Head({ eyebrow, title, sub, center }) {
  return (
    <div className={`s-sec-head ${center ? 'center' : ''}`}>
      {eyebrow && <span className="s-eyebrow">{eyebrow}</span>}
      <h2>{title}</h2>
      {sub && <p>{sub}</p>}
    </div>
  )
}

function PageTop({ eyebrow, title, sub }) {
  return (
    <section className="s-pagetop"><div className="s-wrap">
      <span className="s-eyebrow">{eyebrow}</span>
      <h1>{title}</h1>
      {sub && <p>{sub}</p>}
    </div></section>
  )
}

function Closing({ st, title = 'Meet your new team.', sub = 'Create a workspace, connect your keys and give them their first job.' }) {
  return (
    <section className="s-closing"><div className="s-wrap">
      <h2>{title}</h2>
      <p>{sub}</p>
      <div className="s-cta-row"><Primary st={st} /><Link className="s-btn big" to="/contact">Talk to us</Link></div>
    </div></section>
  )
}

/* ---------- home ---------- */

const PEOPLE = [
  { name: 'Maya', color: '#7a5c8f', role: 'Manager', doing: 'Planning this week’s jobs', live: true },
  { name: 'Sam', color: '#2f7a6b', role: 'Caller', doing: 'On a call with a customer', live: true },
  { name: 'Riley', color: '#b86a2c', role: 'Assistant', doing: 'Drafting a quote for Mr Patel', live: true },
  { name: 'Jo', color: '#486a99', role: 'Bookkeeper', doing: 'Idle', live: false },
]

function HeroMock() {
  return (
    <div className="s-mock" role="img" aria-label="A preview of the Deskmates team page: four AI employees with what each is working on, an email waiting for approval and a call summary.">
      <div className="s-win" aria-hidden="true">
        <div className="s-win-bar"><i /><i /><i /><span>Acme Plumbing</span></div>
        <div className="s-win-body">
          <div className="s-win-side">
            <b><span className="s-mark sm"><i /></span>Deskmates</b>
            {['Team', 'Projects', 'Inbox', 'Apps', 'Settings'].map((n, i) => <span key={n} className={i === 0 ? 'on' : ''}>{n}</span>)}
          </div>
          <div className="s-win-main">
            <div className="s-win-title"><b>Team</b><span>3 working now</span></div>
            {PEOPLE.map((p) => (
              <div className="s-row" key={p.name}>
                <Avatar agent={p} size={34} />
                <div className="s-row-t"><b>{p.name}</b><small>{p.role}</small></div>
                <div className="s-row-d">{p.live && <i className="s-live" />}{p.doing}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="s-float s-float-a" aria-hidden="true">
        <span className="s-float-k"><Icon name="mail" size={14} /> Needs your OK</span>
        <b>Email to 8 customers</b>
        <small>Rule: more than 5 recipients</small>
        <div className="s-float-btns"><span className="s-chip solid">Approve</span><span className="s-chip">Edit</span></div>
      </div>
      <div className="s-float s-float-b" aria-hidden="true">
        <span className="s-float-k"><Icon name="phone" size={14} /> Call summary · Sam</span>
        <b>Booked a boiler service</b>
        <small>Thursday at 10:00 · 2 min 14 s</small>
      </div>
    </div>
  )
}


const DOCK = [['folder', 'Files'], ['mail', 'Mail'], ['calendar', 'Calendar'], ['globe', 'Browser'], ['note', 'Notes'], ['phone', 'Phone']]

/** A picture of an employee's own desktop: mail being written, a calendar behind it, the dock, and their cursor. */
function ComputerMock() {
  return (
    <div className="s-desk" role="img" aria-label="An AI employee's virtual computer: a desktop with a mail window where Riley is writing a quote to a customer, a calendar behind it, and a dock with files, mail, calendar, browser, notes and phone.">
      <div className="s-screen" aria-hidden="true">
        <div className="s-menubar"><span className="s-mark sm"><i /></span><b>Mail</b><span>File</span><span>Edit</span><span>View</span><span className="s-menu-r"><i className="s-live" /> Riley’s computer</span></div>
        <div className="s-desk-area">
          <div className="s-w s-w-cal">
            <div className="s-w-bar"><i /><i /><i /><span>Calendar</span></div>
            <div className="s-cal"><b>Thursday</b>{[['10:00', 'Boiler service, Mr Patel'], ['13:30', 'Call back: Harris Ltd'], ['16:00', 'Send weekly report']].map(([t, n]) => <div key={t}><span>{t}</span>{n}</div>)}</div>
          </div>
          <div className="s-w s-w-mail">
            <div className="s-w-bar"><i /><i /><i /><span>New message</span></div>
            <div className="s-mail">
              <div className="s-mail-f"><span>To</span><b>m.patel@example.com</b></div>
              <div className="s-mail-f"><span>Subject</span><b>Your quote for the annual boiler service</b></div>
              <div className="s-mail-body">Hi Mr Patel, thanks for your call this morning. Please find your quote attached: the annual service is £84, and I can fit you in on Thursday at 10:00<i className="s-caret" /></div>
              <div className="s-mail-foot"><span className="s-att"><Icon name="file" size={13} /> Quote-0412.pdf</span><span className="s-chip solid">Send</span></div>
            </div>
          </div>
          <div className="s-cursor"><svg width="18" height="20" viewBox="0 0 18 20"><path d="M2 1.5v15l4.2-3.8 2.6 5.9 2.6-1.1-2.6-5.8h5.6z" fill="#171717" stroke="#fff" strokeWidth="1.4" strokeLinejoin="round" /></svg><span>Riley</span></div>
        </div>
        <div className="s-dock">{DOCK.map(([i, n]) => <span key={n} title={n}><Icon name={i} size={19} /></span>)}</div>
      </div>
    </div>
  )
}

const DESK_POINTS = [
  ['folder', 'Their own files and mail', 'Everything an employee makes is saved on their own computer first, and shared only when it should be.'],
  ['globe', 'A real browser', 'They read the web, and with your permission fill in forms, on a browser of their own.'],
  ['activity', 'Watch it live, rewind it later', 'See the cursor move and the email being written. Every action is logged by day, with a replay.'],
  ['lock', 'Nothing hidden', 'You can open any employee’s computer at any time and check what was done, and why.'],
]


const shortName = (n) => n.replace(/\s*\(.*?\)\s*/g, ' ').trim()

/** One scrolling line of app logos. The list is repeated once so the loop has no gap. */
function LogoRow({ items, reverse, seconds }) {
  const tile = (l, k) => <span className="s-logo" key={k} title={shortName(l.name)}><img src={`/connector-icons/${l.id}.svg`} alt="" width="32" height="32" loading="lazy" decoding="async" /></span>
  return (
    <div className="s-marq">
      <div className={`s-track ${reverse ? 'rev' : ''}`} style={{ '--dur': `${seconds}s` }}>
        {items.map((l) => tile(l, l.id))}
        <span className="s-dupe">{items.map((l) => tile(l, `d-${l.id}`))}</span>
      </div>
    </div>
  )
}

function AppsSection() {
  const rows = [[], [], []]
  APPS.logos.forEach((l, i) => rows[i % 3].push(l))
  return (
    <section className="s-sec s-apps"><div className="s-wrap">
      <Head eyebrow="Connected apps" title="200+ apps, ready to connect." sub="Your team works inside the tools you already use: CRMs, support desks, payments, calendars, chat and more. Add an app with a key or a sign-in, and switch it on or off whenever you like." center />
      <ul className="s-stats">
        <li><b>{APPS.connectable}</b><span>apps to connect</span></li>
        <li><b>{APPS.builtin}</b><span>built-in tools, no setup</span></li>
        <li><b>{APPS.categories.length}</b><span>categories</span></li>
      </ul>
    </div>
    <div className="s-wall" aria-hidden="true">
      {rows.map((r, i) => <LogoRow key={i} items={r} reverse={i === 1} seconds={[95, 80, 105][i]} />)}
    </div>
    <div className="s-wrap">
      <ul className="s-cats" aria-label="App categories">
        {APPS.categories.map((c) => <li key={c.name}>{c.name}<i>{c.count}</i><span className="s-sr">, {c.count} apps, including {c.examples.join(', ')}</span></li>)}
      </ul>
      <p className="s-apps-note">And anything else through webhooks, MCP servers or a custom API. <Link to="/product">See what your team can do with them <Icon name="arrow" size={14} /></Link></p>
      <p className="s-fine">Logos are trademarks of their respective owners, shown only to identify the service each app connects to.</p>
    </div></section>
  )
}

const DOES = [
  ['mail', 'Email', 'Each employee has their own mailbox. They read, reply and follow up, and anything sensitive can wait for your approval first.'],
  ['phone', 'Voice conversations', 'Natural, short calls on behalf of your company. They say who is calling, never claim to be human, and calls are never recorded.'],
  ['projects', 'Projects and to-dos', 'Give a goal. They plan it, split it into tasks, do the work and hand you the finished files. You watch it move on a board.'],
  ['mic', 'Meetings by voice', 'Talk to your team out loud. Ask one person or everyone, and what you ask for lands on the right to-do list.'],
  ['apps', 'Your apps', 'Connect over 200 apps, from CRMs and support desks to payments and calendars. Forty more work with nothing to set up.'],
  ['computer', 'A virtual computer each', 'Every employee has their own desktop with files, mail, a calendar and a browser. Open it and watch them work.'],
]

const CONTROL = [
  ['Approval rules', 'Hold any email to more than five people, outside your company, or with an attachment, until you say yes.'],
  ['Spend limits', 'Daily caps for the whole company and for each employee. Hit a cap and the team pauses until tomorrow.'],
  ['A full audit log', 'Every sign-in, change and approval is recorded, and you can export it.'],
  ['Calls never recorded', 'Only a written summary is kept, and you can have even that deleted automatically.'],
  ['Two-factor sign-in', 'Add an authenticator app. Your API keys are encrypted and never shown again.'],
  ['Backups you can restore', 'Daily backups of the whole workspace, with one-click restore.'],
]

const TEAMS = [
  ['Starter trio', 'A manager, a caller and an assistant: the smallest team that runs itself.'],
  ['Sales', 'Finds leads, follows up and writes proposals.'],
  ['Customer support', 'Answers customers and escalates what it cannot solve.'],
  ['Back office', 'Email, scheduling, documents, spreadsheets and bookkeeping admin.'],
  ['Marketing', 'Plans and writes content and campaigns, and reports on what worked.'],
  ['Recruiting', 'Writes job posts, screens applicants and books interviews.'],
]

const FAQ = [
  ['Do I need my own OpenAI account?', 'Yes. Each company connects its own OpenAI key, and optionally an ElevenLabs key for phone-call voices, so you pay those providers directly for what your team uses. Spend limits keep it predictable.'],
  ['Will my team pretend to be human?', 'No. Calls begin with who is calling and from which company, and if someone sincerely asks whether they are speaking to a person, the employee says it is an AI.'],
  ['Are calls recorded?', 'Never. Only a written transcript and summary are kept, and you can set those to delete automatically.'],
  ['Is my company’s data kept apart from other companies?', 'Yes. Every company has its own database, files, keys and apps. Nothing is shared between companies.'],
  ['Can the team email people without my knowing?', 'Only as far as you allow. Set rules so emails wait for your approval, and every action is logged.'],
  ['What is the virtual computer?', 'Each employee has their own desktop inside Deskmates, with their files, mail, calendar, browser, notes and phone. It is a window onto the work they do, so you can watch live and look back at what they did.'],
  ['What can I connect?', 'Over 200 apps, such as CRMs, support desks, payments, calendars and chat, plus 40 built-in tools that need no account at all.'],
]

export function Home({ st }) {
  return (
    <>
      <section className="s-hero"><div className="s-wrap s-hero-grid">
        <div className="s-hero-copy">
          {st?.authenticated ? <>
          <span className="s-pill"><i /> AI employees for your business</span>
          <h1>Hire an AI team that gets the work done.</h1>
          <p className="s-lead">Deskmates gives your business a team of AI employees. Each one has a name, an email address, a phone and a virtual computer of their own. They handle email, plan and run projects, write documents and hold voice conversations, while you stay in charge.</p>
          <div className="s-cta-row"><Primary st={st} /><Link className="s-btn big" to="/product">See what it does</Link></div>
          <ul className="s-trust" aria-label="Key points">
            <li><Icon name="check" size={15} /> Bring your own OpenAI key</li>
            <li><Icon name="check" size={15} /> Calls are never recorded</li>
            <li><Icon name="check" size={15} /> Emails can wait for your approval</li>
          </ul>
          </> : <div className="employee-mac">
          <div className="employee-macbar" aria-hidden="true">
            <span className="employee-mac-dots"><i /><i /><i /></span>
            <span className="employee-mac-title">Deskmates — AI employees</span>
            <span className="employee-mac-mark">⌘</span>
          </div>
          <div className="employee-macscreen">
          <span className="employee-kicker">AI employees for your business</span>
          <h1 className="employee-title">Hire an AI team<br />that gets the work<br /><em>done.</em></h1>
          <p className="s-lead employee-lead">Deskmates gives your business a team of AI employees. Each one has a name, an email address, a phone and a virtual computer of their own. They handle email, plan and run projects, write documents and hold voice conversations, while you stay in charge.</p>
          <div className="s-cta-row employee-ctas"><Primary st={st} /><Link className="s-btn big employee-secondary" to="/product">See what it does <Icon name="arrow" size={16} /></Link></div>
          <ul className="s-trust employee-trust" aria-label="Key points">
            <li><Icon name="check" size={15} /> Bring your own OpenAI key</li>
            <li><Icon name="check" size={15} /> Calls are never recorded</li>
            <li><Icon name="check" size={15} /> Emails can wait for your approval</li>
          </ul>
          </div>
          </div>}
        </div>
        <HeroMock />
      </div></section>

      <section className="s-sec s-desk-sec"><div className="s-wrap s-desk-grid">
        <div className="s-desk-copy">
          <span className="s-eyebrow">Virtual computers</span>
          <h2>Every employee gets their own computer.</h2>
          <p className="s-desk-lead">Not a chat window. Each AI employee works at a desk of their own, with files, mail, a calendar, a browser, notes and a phone. Open it any time and watch the work happen.</p>
          <ul className="s-desk-points">
            {DESK_POINTS.map(([icon, t, d]) => <li key={t}><span className="s-ico"><Icon name={icon} size={18} /></span><div><b>{t}</b><p>{d}</p></div></li>)}
          </ul>
        </div>
        <ComputerMock />
      </div></section>

      <section className="s-sec"><div className="s-wrap">
        <Head eyebrow="What it does" title="The everyday work of a small team, handled." sub="Not a chatbot. A team of named employees who keep their own to-do lists, work through them and tell you when it is done." />
        <div className="s-grid3">
          {DOES.map(([icon, t, d]) => (
            <article className="s-card" key={t}><span className="s-ico"><Icon name={icon} size={20} /></span><h3>{t}</h3><p>{d}</p></article>
          ))}
        </div>
        <p className="s-more"><Link to="/product">Explore everything it can do <Icon name="arrow" size={15} /></Link></p>
      </div></section>

      <AppsSection />

      <section className="s-sec s-alt"><div className="s-wrap">
        <Head eyebrow="How it works" title="Up and running in three steps." center />
        <ol className="s-steps">
          <li><span className="s-num">1</span><h3>Create your workspace</h3><p>Your company gets its own private space. Pick a ready-made team or add people yourself.</p></li>
          <li><span className="s-num">2</span><h3>Connect your keys</h3><p>Paste your own OpenAI key, and an ElevenLabs key for phone voices if you want them. Each is checked and stored encrypted.</p></li>
          <li><span className="s-num">3</span><h3>Give them work</h3><p>Talk to them, email them or write a to-do. They plan it, do it and report back.</p></li>
        </ol>
      </div></section>

      <section className="s-sec"><div className="s-wrap s-split">
        <div>
          <Head eyebrow="Trust" title="You stay in charge." sub="A capable team is only useful if you can trust it. Deskmates is built around that: you set the rules, and everything is on the record." />
          <p className="s-more left"><Link to="/security">Read about security <Icon name="arrow" size={15} /></Link></p>
        </div>
        <ul className="s-checks">
          {CONTROL.map(([t, d]) => <li key={t}><span className="s-tick"><Icon name="check" size={14} /></span><div><b>{t}</b><p>{d}</p></div></li>)}
        </ul>
      </div></section>

      <section className="s-sec s-alt"><div className="s-wrap">
        <Head eyebrow="Start fast" title="Begin with a team that fits." sub="Choose a ready-made team when you sign up, or build your own one person at a time. Each employee writes their own name and personality." />
        <div className="s-grid3 tight">
          {TEAMS.map(([t, d]) => <article className="s-card slim" key={t}><h3>{t}</h3><p>{d}</p></article>)}
        </div>
      </div></section>

      <section className="s-mission"><div className="s-wrap">
        <span className="s-eyebrow light">Our mission</span>
        <p className="s-mission-text">Give every small business a capable, trustworthy team, without the cost and complexity of a big one.</p>
        <Link className="s-btn light" to="/about">About Deskmates <Icon name="arrow" size={15} /></Link>
      </div></section>

      <section className="s-sec"><div className="s-wrap s-faq-wrap">
        <Head eyebrow="Questions" title="Straight answers." />
        <div className="s-faq">
          {FAQ.map(([q, a]) => <details key={q}><summary>{q}<Icon name="chevD" size={16} /></summary><p>{a}</p></details>)}
        </div>
      </div></section>

      <Closing st={st} />
    </>
  )
}

/* ---------- product ---------- */

const FEATURES = [
  ['users', 'A team with names and roles', 'Every employee has their own name, personality, work email, to-do list, calendar, files and phone.', ['Add people one at a time or start from a ready-made team', 'Make anyone a manager: they can hire, assign, review and report for you', 'Admin accounts and passwords always stay with you']],
  ['computer', 'A virtual computer for every employee', 'Each employee gets their own desktop: files, mail, to-dos, calendar, phone, browser and notes. Open it to see what they are doing.', ['Watch live as the cursor moves, and replay it afterwards', 'A full history by day and type of action', 'Teach them a task once and they remember how']],
  ['mail', 'Email that works like email', 'A complete mail client for you, and a mailbox for each employee.', ['Folders, search, conversations, reply and forward', 'Emails to outside addresses can wait in “To approve”', 'Rules by recipients, words, domains and attachments']],
  ['phone', 'Voice conversations', 'Short, natural calls for your company, with voices from ElevenLabs. Try them in the built-in softphone in your browser today; connecting real phone lines is on the way.', ['They say who is calling and never claim to be human', 'Calls are never recorded; only a summary is kept', 'If a caller asks something new, they put them on hold and ask you at once']],
  ['mic', 'Meetings by voice', 'Talk to your team out loud, one voice at a time.', ['Name a person, or say “everyone”', 'What you ask for goes on the right to-do list', 'They can join Google Meet, Zoom or Teams as a note-taker through Recall.ai']],
  ['projects', 'Projects and to-dos', 'Give a goal and the team plans it, splits it into tasks and works through them in order.', ['A board from To do through Planning and Executing to Done', 'A shared drive for finished files and a feed of updates', 'Duplicate and runaway tasks are stopped for you']],
  ['apps', 'Over 200 connected apps', 'CRMs, support desks, payments, calendars, chat, accounting and more, added with a key or sign-in.', ['Each app has an on/off switch and a “may make changes” switch', 'Forty built-in tools need nothing: weather, maps, research, company records and more', 'Anything else through webhooks or a custom API']],
  ['globe', 'A browser of their own', 'Employees can read web pages in a real browser, and with your permission fill in forms.', ['Read-only by default; switch it off or to full access', 'Saved logins are kept encrypted and typed in by the server', 'Private and internal addresses are blocked']],
  ['grid', 'Ready-made templates', 'Skip the blank page with whole teams, project plans and playbooks.', ['Teams for sales, support, back office, marketing and recruiting', 'Projects such as a promotion, onboarding or monthly books', 'Playbooks for refunds, quotes, complaints and invoices']],
  ['chart', 'See how it is going', 'Know what it costs and what got done.', ['A scorecard for each employee, made only of counts you can check', 'Spend by employee and by day, with daily caps', 'A daily digest and instant alerts by email, Slack, Discord or Telegram']],
]

export function Product({ st }) {
  return (
    <>
      <PageTop eyebrow="Product" title="Everything your AI team can do." sub="One place where a team of AI employees reads and writes email, plans projects, holds voice conversations, and uses your tools, with you in control of all of it." />
      <section className="s-sec tight-top"><div className="s-wrap s-grid2">
        <h2 className="s-sr">What your team can do</h2>
        {FEATURES.map(([icon, t, d, list]) => (
          <article className="s-card big" key={t}>
            <span className="s-ico"><Icon name={icon} size={20} /></span>
            <h3>{t}</h3>
            <p>{d}</p>
            <ul>{list.map((x) => <li key={x}><Icon name="check" size={14} />{x}</li>)}</ul>
          </article>
        ))}
      </div></section>
      <Closing st={st} />
    </>
  )
}

/* ---------- security ---------- */

const SEC = [
  ['folder', 'Your data stays yours', ['Every company has its own database, files, keys and apps. Nothing is shared between companies.', 'A sign-in only ever opens the company it belongs to, and is refused everywhere else.', 'Whoever runs the service sees a company’s name, owner and when it was last used, never its data.']],
  ['lock', 'Signing in', ['Passwords are stored as Argon2id hashes, must be at least 12 characters, and common passwords are refused.', 'Optional two-factor sign-in with an authenticator app, plus one-time backup codes.', 'Five wrong passwords lock that account from that address for 15 minutes. Sessions end after 12 hours, or 2 hours idle, and you can sign out other devices.']],
  ['key', 'Your keys', ['OpenAI and ElevenLabs keys are checked with the provider, then stored encrypted.', 'They are never shown again and never leave the server.']],
  ['sliders', 'Control over what the team does', ['Approval rules hold emails until you say yes, and any employee can be set to need approval for every outside email.', 'Daily spend caps for the company and for each employee, and a Pause button that stops all work at once.', 'Customer data, uploaded documents and what callers say are treated as untrusted, never as instructions. Deletions can only be ordered from your own chats and meetings.']],
  ['phone', 'Calls and privacy', ['Calls are never recorded; only a written transcript and summary are kept.', 'Set transcripts to delete automatically, or erase everything about one person.', 'Callers are told who is calling, and the AI never claims to be human when sincerely asked.']],
  ['shield', 'Records and recovery', ['An audit log of sign-ins, changes, approvals and key changes, with CSV export.', 'Daily backups of the whole workspace, one-click restore, and the option to close the account and erase everything.', 'Security headers, cross-site request protection, rate limits and upload limits on every request.']],
]

export function Security({ st }) {
  return (
    <>
      <PageTop eyebrow="Security and privacy" title="Built so you can trust your team." sub="A team that can email, call and spend money on your behalf has to be safe to run. Here is exactly how Deskmates protects you." />
      <section className="s-sec tight-top"><div className="s-wrap s-grid2">
        <h2 className="s-sr">How we protect you</h2>
        {SEC.map(([icon, t, list]) => (
          <article className="s-card big" key={t}>
            <span className="s-ico"><Icon name={icon} size={20} /></span>
            <h3>{t}</h3>
            <ul>{list.map((x) => <li key={x}><Icon name="check" size={14} />{x}</li>)}</ul>
          </article>
        ))}
      </div></section>
      <Closing st={st} title="Safe to start small." sub="Create a workspace, set your rules and your limits, then let the team in gradually." />
    </>
  )
}

/* ---------- about ---------- */

const PRINCIPLES = [
  ['You stay in charge', 'The team works for you. You set the rules, the limits and who can approve what, and everything they do is on the record.'],
  ['Honest by design', 'Our employees say who they are. They never claim to be human when sincerely asked, and calls start with who is calling and from where.'],
  ['Private by default', 'Calls are never recorded. Each company’s data is kept apart, and your keys are yours.'],
  ['Plain and simple', 'No jargon and no maze of settings. If a small-business owner cannot set it up in an afternoon, we have got it wrong.'],
]

export function About({ st }) {
  return (
    <>
      <PageTop eyebrow="About" title="A capable team for every small business." sub="Big companies hire people for the work that fills a small business owner’s evenings. We think everyone should have that help." />
      <section className="s-mission flat"><div className="s-wrap">
        <span className="s-eyebrow light">Our mission</span>
        <p className="s-mission-text">Give every small business a capable, trustworthy team, without the cost and complexity of a big one.</p>
      </div></section>
      <section className="s-sec"><div className="s-wrap s-narrow">
        <Head eyebrow="Why we are building it" title="The work never stops, and it is rarely the work you started the business for." />
        <p className="s-prose">Chasing email. Following up quotes. Updating the spreadsheet. Calling the customer back. Writing the same document for the fourth time. It is real work, it has to be done well, and it is exactly the kind of work a capable AI employee can take on.</p>
        <p className="s-prose">But handing work to software only makes sense if you can trust it. So Deskmates is built the other way round from most AI tools: a team you can see, with names and to-do lists, rules you control, limits on what they spend, and a record of everything they do.</p>
      </div></section>
      <section className="s-sec s-alt"><div className="s-wrap">
        <Head eyebrow="What we hold to" title="Four principles." center />
        <div className="s-grid4">
          {PRINCIPLES.map(([t, d], i) => <article className="s-card" key={t}><span className="s-num">{i + 1}</span><h3>{t}</h3><p>{d}</p></article>)}
        </div>
      </div></section>
      <section className="s-sec"><div className="s-wrap">
        <Head eyebrow="Who it is for" title="Small teams with a lot to do." />
        <div className="s-tags">
          {['Trades and local services', 'Clinics and practices', 'Agencies and consultancies', 'Online shops', 'Property and lettings', 'Small offices'].map((x) => <span key={x}>{x}</span>)}
        </div>
      </div></section>
      <Closing st={st} />
    </>
  )
}

/* ---------- contact ---------- */

const TOPICS = [['general', 'General question'], ['support', 'Help with my account'], ['security', 'Security report'], ['partner', 'Partnership'], ['other', 'Something else']]

export function Contact({ st }) {
  const [f, setF] = useState({ name: '', email: '', topic: 'general', message: '', website: '' })
  const [state, setState] = useState('idle')  // idle | sending | sent
  const [err, setErr] = useState('')
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const send = async (e) => {
    e.preventDefault(); setErr(''); setState('sending')
    try { await api.post('/contact', f); setState('sent') } catch (x) { setErr(x.message); setState('idle') }
  }
  const email = st?.contact_email
  return (
    <>
      <PageTop eyebrow="Contact" title="We would like to hear from you." sub="Questions about Deskmates, help with your account, a security report or a partnership: write to us and we will reply by email." />
      <section className="s-sec tight-top"><div className="s-wrap s-contact">
        <div className="s-contact-side">
          <h2 className="s-sr">Ways to reach us</h2>
          <div className="s-card slim"><h3>Before you write</h3><p>Already have an account? Most answers are inside, under Settings. Security reports are read first.</p></div>
          {email && <div className="s-card slim"><h3>Email</h3><p><a className="s-textlink" href={`mailto:${email}`}>{email}</a></p></div>}
          <div className="s-card slim"><h3>What happens next</h3><p>Your message goes to the people who run this service. We reply to the email address you give.</p></div>
        </div>
        {state === 'sent' ? (
          <div className="s-form s-sent" role="status">
            <span className="s-ico ok"><Icon name="check" size={22} /></span>
            <h2>Message sent</h2>
            <p>Thank you. We will reply to {f.email} as soon as we can.</p>
            <Link className="s-btn" to="/">Back to home</Link>
          </div>
        ) : (
          <form className="s-form" onSubmit={send} noValidate>
            <div className="s-two">
              <label><span className="s-l">Your name <i>(optional)</i></span><input autoComplete="name" value={f.name} onChange={set('name')} maxLength={100} /></label>
              <label><span className="s-l">Email</span><input type="email" autoComplete="email" required value={f.email} onChange={set('email')} maxLength={200} /></label>
            </div>
            <label><span className="s-l">What is it about?</span><select value={f.topic} onChange={set('topic')}>{TOPICS.map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></label>
            <label><span className="s-l">Message</span><textarea rows={7} required value={f.message} onChange={set('message')} maxLength={5000} /></label>
            <div className="s-hp" aria-hidden="true"><label><span className="s-l">Leave this empty</span><input tabIndex={-1} autoComplete="off" value={f.website} onChange={set('website')} /></label></div>
            {err && <p className="s-err" role="alert">{err}</p>}
            <button className="s-btn solid big" disabled={state === 'sending' || !f.email.trim() || f.message.trim().length < 10}>{state === 'sending' ? 'Sending…' : 'Send message'}</button>
          </form>
        )}
      </div></section>
    </>
  )
}

/* ---------- privacy and terms ---------- */

const UPDATED = '15 October 2026'

const LEGAL = {
  privacy: {
    title: 'Privacy policy',
    intro: 'This explains what Deskmates collects, why, who else handles it, and how you can delete it. In this policy, “we” means whoever operates this Deskmates service.',
    sections: [
      ['What we collect', ['Your account: your email address, your company name and a hash of your password (never the password itself).', 'What your company puts in its workspace: employees, emails, call transcripts and summaries, files, projects, settings and the apps and keys you connect (keys are stored encrypted).', 'Security records: sign-ins and changes, with the IP address and browser type.', 'Messages you send through the contact form: your name, email and message.']],
      ['What we do not do', ['We do not sell your data, show advertising or run analytics or tracking scripts.', 'We do not record calls. Only a written transcript and summary are kept.', 'This site loads no fonts, scripts or images from other companies.']],
      ['Who else handles your data', ['Your AI team sends the text and speech it works on to OpenAI and, if you enable phone voices, to ElevenLabs. This uses your own accounts and is covered by your agreement with each of them.', 'Apps you connect (such as a CRM or a calendar) receive what your team sends them.', 'We do not send workspace data anywhere else.']],
      ['Cookies and storage', ['One session cookie and one security-token cookie, both essential to staying signed in.', 'Your light or dark theme choice, saved in your browser’s local storage.', 'No advertising or tracking cookies.']],
      ['How long we keep it', ['Workspace data is kept until you delete it. You can set call transcripts to delete automatically.', 'Closing your account (Settings, Company) permanently deletes the workspace, its files and its keys.', 'Contact messages are kept until we have dealt with them.']],
      ['Your choices', ['Download a backup of everything in your workspace at any time.', 'Erase everything about one person, or close the whole account.', 'Turn on two-factor sign-in and review the audit log.', 'Ask us anything about your data through the contact form.']],
      ['Changes', ['If we change this policy we will update the date above. Material changes will be announced inside the product.']],
    ],
  },
  terms: {
    title: 'Terms of use',
    intro: 'These terms apply when you use Deskmates. By creating a workspace you agree to them on behalf of your company.',
    sections: [
      ['Your account', ['You must be able to bind your company to these terms, and keep your sign-in details safe.', 'You are responsible for what happens in your workspace, including what your AI employees do on your behalf.']],
      ['Your content', ['Your workspace content belongs to you. We process it only to provide the service.']],
      ['AI can be wrong', ['AI employees can make mistakes or miss things. Review important work, and use approval rules for anything that matters.', 'Deskmates is not legal, medical, financial or other professional advice.']],
      ['Acceptable use', ['Do not use Deskmates for anything unlawful, deceptive or harmful, or to send spam.', 'Follow the laws on calling, texting, email marketing, consent and do-not-call lists wherever your customers are.', 'Do not impersonate anyone or tell people the AI is human, and do not try to reach another company’s data or overload the service.']],
      ['Other services and costs', ['OpenAI, ElevenLabs and any apps you connect have their own terms. You pay them directly for what you use.']],
      ['Availability', ['The service is provided as is and as available, and may be interrupted. Use the backup tool regularly.']],
      ['Suspension and closing', ['You can close your account at any time under Settings.', 'We may suspend a workspace that breaks these terms or puts the service or others at risk.']],
      ['Liability', ['To the extent the law allows, we are not liable for indirect or consequential losses, or for losses caused by AI output you did not review. Nothing here limits liability that cannot be limited by law.']],
      ['Changes and contact', ['We may update these terms and will change the date above when we do. Questions go through the contact form.']],
    ],
  },
}

export function Legal({ which }) {
  const d = LEGAL[which]
  return (
    <>
      <PageTop eyebrow="Legal" title={d.title} sub={`Last updated ${UPDATED}`} />
      <section className="s-sec tight-top"><div className="s-wrap s-narrow s-legal">
        <p className="s-prose">{d.intro}</p>
        {d.sections.map(([h, items]) => (
          <div key={h}>
            <h2>{h}</h2>
            {items.length === 1 ? <p className="s-prose">{items[0]}</p> : <ul>{items.map((x) => <li key={x}>{x}</li>)}</ul>}
          </div>
        ))}
        <p className="s-prose">Questions? <Link to="/contact">Contact us</Link>.</p>
      </div></section>
    </>
  )
}
