import postgres from "npm:postgres"

// Connection reused across warm invocations
let sql: ReturnType<typeof postgres> | null = null

function getDb() {
  if (!sql) {
    sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, {
      ssl: "require",
      max: 3,
      idle_timeout: 20,
      connect_timeout: 10,
    })
  }
  return sql
}

// JWT verification is handled by Supabase gateway (deployed without --no-verify-jwt).
// Only callers with a valid service_role or anon JWT can reach this function.
Deno.serve(async (req: Request) => {
  let body: { query: string; params?: unknown[] }
  try {
    body = await req.json()
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })
  }

  const { query, params = [] } = body
  if (!query || typeof query !== "string") {
    return new Response(JSON.stringify({ error: "Missing query" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })
  }

  try {
    const db = getDb()
    const result = await db.unsafe(query, params as unknown[])
    const raw = result as unknown as { command: string; count?: number }
    const command = raw.command ?? ""
    const rowCount: number =
      typeof raw.count === "number"
        ? raw.count
        : Array.isArray(result)
        ? result.length
        : 0
    // Build asyncpg-style command tag: "DELETE 3", "INSERT 0 1", etc.
    const tag =
      command === "INSERT"
        ? `INSERT 0 ${rowCount}`
        : `${command} ${rowCount}`
    const rows = Array.isArray(result) ? result.map((r) => ({ ...r })) : []
    return new Response(JSON.stringify({ rows, command: tag }), {
      headers: { "Content-Type": "application/json" },
    })
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    return new Response(JSON.stringify({ error: msg }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    })
  }
})
