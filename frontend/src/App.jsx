import { useCallback, useEffect, useMemo, useState } from 'react'
import api, { ApiError } from './api.js'

const OUTCOME_TEXT = {
  found: 'Resolved to one drawer.',
  ambiguous: 'Several items match. Pick one below.',
  not_found: 'No item matches that search.',
  unlocated: 'That item is known, but no drawer is assigned to it yet.',
}

function ErrorBanner({ error, onRetry }) {
  if (!error) return null
  const offline = error.kind === 'network' || error.kind === 'timeout'
  return (
    <div className={`banner ${offline ? 'banner-offline' : 'banner-error'}`} role="alert">
      <strong>{offline ? 'Backend unreachable' : 'Error'}</strong>
      <span>{error.message}</span>
      {error.status ? <code>HTTP {error.status}</code> : null}
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  )
}

function ControllerCards({ controllers, litDrawer }) {
  return (
    <div className="cards">
      {controllers.map((c) => {
        const owns = litDrawer !== null && litDrawer >= c.drawer_start && litDrawer <= c.drawer_end
        return (
          <article key={c.controller_id} className={`card status-${c.status}`}>
            <header>
              <h3>{c.controller_id}</h3>
              <span className={`pill pill-${c.status}`}>{c.status}</span>
            </header>
            <dl>
              <div>
                <dt>Drawers</dt>
                <dd>
                  {c.drawer_start}&ndash;{c.drawer_end}
                </dd>
              </div>
              <div>
                <dt>LEDs</dt>
                <dd>{c.led_count}</dd>
              </div>
              <div>
                <dt>Firmware</dt>
                <dd>{c.fw_version || 'not reported'}</dd>
              </div>
              <div>
                <dt>Last seen</dt>
                <dd>{c.last_seen ? new Date(c.last_seen).toLocaleString() : 'never'}</dd>
              </div>
            </dl>
            {owns ? <p className="card-note">Target of the current locate command.</p> : null}
          </article>
        )
      })}
    </div>
  )
}

function DrawerMap({ map, litDrawer, onSelect, busyDrawer }) {
  const grouped = useMemo(() => {
    const byController = new Map()
    for (const cell of map.drawers) {
      if (!byController.has(cell.controller_id)) byController.set(cell.controller_id, [])
      byController.get(cell.controller_id).push(cell)
    }
    return [...byController.entries()]
  }, [map])

  return (
    <div className="map">
      {grouped.map(([controllerId, cells]) => (
        <section key={controllerId} className="map-group">
          <h4>
            {controllerId}
            <span>
              drawers {cells[0].drawer_number}&ndash;{cells[cells.length - 1].drawer_number}
            </span>
          </h4>
          <div className="grid">
            {cells.map((cell) => (
              <button
                key={cell.drawer_number}
                type="button"
                className={`cell${cell.drawer_number === litDrawer ? ' cell-lit' : ''}${
                  cell.item_count === 0 ? ' cell-empty' : ''
                }`}
                disabled={busyDrawer !== null}
                onClick={() => onSelect(cell.drawer_number)}
                title={`Drawer ${cell.drawer_number} - ${cell.controller_id} LED ${cell.local_led_index} - ${cell.item_count} item(s)`}
              >
                <span className="cell-number">{cell.drawer_number}</span>
                <span className="cell-led">LED {cell.local_led_index}</span>
                {busyDrawer === cell.drawer_number ? <span className="cell-spinner" /> : null}
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

export default function App() {
  const [architecture, setArchitecture] = useState(null)
  const [controllers, setControllers] = useState([])
  const [map, setMap] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [loading, setLoading] = useState(true)

  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState(null)
  const [searchError, setSearchError] = useState(null)

  const [busyDrawer, setBusyDrawer] = useState(null)
  const [command, setCommand] = useState(null)
  const [locateError, setLocateError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [arch, ctrls, drawerMap] = await Promise.all([
        api.architecture(),
        api.controllers(),
        api.drawerMap(),
      ])
      setArchitecture(arch)
      setControllers(ctrls)
      setMap(drawerMap)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err : new ApiError(String(err)))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function onSearch(event) {
    event.preventDefault()
    setSearchError(null)
    setResult(null)
    setCommand(null)
    setLocateError(null)
    if (!query.trim()) {
      setSearchError(new ApiError('Type something to search for.', { kind: 'http', status: 400 }))
      return
    }
    setSearching(true)
    try {
      setResult(await api.search(query))
    } catch (err) {
      setSearchError(err instanceof ApiError ? err : new ApiError(String(err)))
    } finally {
      setSearching(false)
    }
  }

  async function locateDrawer(drawerNumber) {
    setBusyDrawer(drawerNumber)
    setLocateError(null)
    setCommand(null)
    try {
      const response = await api.locate({ drawer_number: drawerNumber })
      setCommand(response)
    } catch (err) {
      setLocateError(err instanceof ApiError ? err : new ApiError(String(err)))
    } finally {
      setBusyDrawer(null)
    }
  }

  const litDrawer = command && command.command ? command.command.drawer_number : null

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>FindIt</h1>
          <p className="subtitle">
            {architecture
              ? `${architecture.topology} - ${architecture.total_drawers} drawers`
              : 'Loading architecture...'}
          </p>
        </div>
        <button type="button" className="ghost" onClick={load} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </header>

      <ErrorBanner error={loadError} onRetry={load} />

      <section className="panel">
        <h2>Search</h2>
        <form onSubmit={onSearch} className="search">
          <input
            type="search"
            value={query}
            placeholder="e.g. spark max, neopixel, m3"
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Item search"
          />
          <button type="submit" disabled={searching}>
            {searching ? 'Searching...' : 'Search'}
          </button>
        </form>

        <ErrorBanner error={searchError} />

        {result ? (
          <div className={`result result-${result.outcome}`}>
            <p className="result-headline">
              <span className={`pill pill-${result.outcome}`}>{result.outcome}</span>
              {OUTCOME_TEXT[result.outcome] || 'Unexpected outcome.'}
            </p>

            {result.route ? (
              <div className="resolved">
                <div>
                  <span className="label">Drawer</span>
                  <strong>{result.route.drawer_number}</strong>
                </div>
                <div>
                  <span className="label">Controller</span>
                  <strong>{result.route.controller_id}</strong>
                </div>
                <div>
                  <span className="label">Local LED</span>
                  <strong>{result.route.led_index}</strong>
                </div>
                <button
                  type="button"
                  className="primary"
                  disabled={busyDrawer !== null}
                  onClick={() => locateDrawer(result.route.drawer_number)}
                >
                  {busyDrawer === result.route.drawer_number ? 'Locating...' : 'Light this drawer'}
                </button>
              </div>
            ) : null}

            {result.candidates && result.candidates.length > 1 ? (
              <ul className="candidates">
                {result.candidates.map((c) => (
                  <li key={c.item_id}>
                    <span>{c.name}</span>
                    {c.drawer_number ? (
                      <button
                        type="button"
                        disabled={busyDrawer !== null}
                        onClick={() => locateDrawer(c.drawer_number)}
                      >
                        Drawer {c.drawer_number}
                      </button>
                    ) : (
                      <em>no drawer assigned</em>
                    )}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <ErrorBanner error={locateError} />

        {command ? (
          <div className={`command command-${command.command.status}`}>
            <p>
              <span className={`pill pill-${command.command.status}`}>{command.command.status}</span>
              {command.note}
            </p>
            <dl>
              <div>
                <dt>Command</dt>
                <dd>
                  <code>{command.command.command_id}</code>
                </dd>
              </div>
              <div>
                <dt>Target</dt>
                <dd>
                  drawer {command.command.drawer_number} &rarr; {command.command.controller_id} LED{' '}
                  {command.command.led_index}
                </dd>
              </div>
              <div>
                <dt>Published</dt>
                <dd>{command.published ? 'yes' : 'no'}</dd>
              </div>
              <div>
                <dt>Acknowledged</dt>
                <dd>{command.command.acked_at ? command.command.acked_at : 'not yet'}</dd>
              </div>
            </dl>
            {command.command.error ? <p className="command-error">{command.command.error}</p> : null}
            {!command.command.acked_at ? (
              <p className="hint">
                A published command is not a lit LED. Until a controller acknowledges, this is
                unconfirmed.
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h2>Controllers</h2>
        {loading && !controllers.length ? (
          <p className="muted">Loading controllers...</p>
        ) : controllers.length ? (
          <ControllerCards controllers={controllers} litDrawer={litDrawer} />
        ) : (
          <p className="muted">No controllers returned by the backend.</p>
        )}
      </section>

      <section className="panel">
        <h2>
          Drawer map
          {map ? <span className="muted"> ({map.drawers.length} cells)</span> : null}
        </h2>
        {loading && !map ? (
          <p className="muted">Loading drawer map...</p>
        ) : map ? (
          <DrawerMap
            map={map}
            litDrawer={litDrawer}
            busyDrawer={busyDrawer}
            onSelect={locateDrawer}
          />
        ) : (
          <p className="muted">No drawer map available.</p>
        )}
      </section>

      <footer className="app-footer">
        <span>API: {api.baseUrl}</span>
      </footer>
    </div>
  )
}
