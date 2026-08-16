/**
 * Cloudflare Worker: relays Health Auto Export's REST API automation POST to
 * GitHub's repository_dispatch endpoint, wrapping it in the envelope that
 * endpoint requires ({event_type, client_payload}) since Health Auto Export
 * cannot template its request body itself.
 *
 * Deploy: Cloudflare dashboard -> Workers & Pages -> Create Worker -> paste
 * this file's contents -> Deploy.
 *
 * Required secrets/vars (Worker -> Settings -> Variables):
 *   GITHUB_PAT     (secret) - PAT with permission to trigger repository_dispatch
 *   SHARED_SECRET  (secret) - any random string; must match the header value
 *                              configured in Health Auto Export's automation
 *   GH_OWNER       (var)    - e.g. "morinaoden"
 *   GH_REPO        (var)    - e.g. "fitness"
 */
export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405 });
    }

    const providedSecret = request.headers.get('X-Health-Export-Secret');
    if (!env.SHARED_SECRET || providedSecret !== env.SHARED_SECRET) {
      return new Response('Unauthorized', { status: 401 });
    }

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return new Response('Invalid JSON body', { status: 400 });
    }

    const githubUrl = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`;
    const githubResp = await fetch(githubUrl, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GITHUB_PAT}`,
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'health-export-relay-worker'
      },
      body: JSON.stringify({
        event_type: 'health-export',
        client_payload: body
      })
    });

    if (!githubResp.ok) {
      const errText = await githubResp.text();
      return new Response(`GitHub dispatch failed: ${githubResp.status} ${errText}`, { status: 502 });
    }

    return new Response('OK', { status: 200 });
  }
};
