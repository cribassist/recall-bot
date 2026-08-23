# Recall Bot — Setup Checklist

Everything the bot needs (CPSC data) is free with no signup. The only accounts
you need are one for X (to post) and one for GitHub (to host the free cron job).

## 1. Create the X account
1. Make a dedicated account for this bot (don't run it on your personal handle).
2. Give it a clear name/bio, e.g. "Product Recall Alerts — safety recalls as
   the CPSC announces them."
3. Label it as automated: on X, go to **Settings → Your Account → Automation**,
   and connect this bot account to your personal (managing) account. This is
   what makes the "Automated by @you" badge appear — required by X's policy.

## 2. Set up the X Developer app
1. Go to developer.x.com → your project/app (the one with your $5 balance).
2. Under **User authentication settings**, set:
   - App permissions: **Read and Write**
   - Type of app: **Web App, Automated App or Bot**
3. Generate and save these four values (you'll only see the secrets once):
   - API Key
   - API Key Secret
   - Access Token
   - Access Token Secret
   (If you generated Access Token/Secret *before* switching permissions to
   Read+Write, regenerate them — old tokens keep the old permission level.)

## 3. Create the GitHub repo
1. github.com → New repository → name it (e.g. `recall-bot`) → **Public**
   (public repos get unlimited free Actions minutes).
2. Upload all the files from this project, keeping the folder structure —
   `.github/workflows/recall-bot.yml` must stay in that exact path.

## 4. Add your X keys as repo secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add all four, exact names:
- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`

## 5. Test it
1. Go to the **Actions** tab → "Recall Bot" workflow → **Run workflow** (this
   is the `workflow_dispatch` trigger — runs it immediately instead of waiting
   for the schedule).
2. Check the run log. You should see something like
   `Fetched N recalls... Posted X new recall(s).`
3. Check the X account — you should see the new post(s).
4. If it fails, the log will show the exact error (bad keys, permission issue,
   etc.) — the script deliberately never fails silently.

## 6. Let it run
Once it works, it runs itself every 6 hours automatically. No app open, no
computer on — GitHub's servers run it for free.

## Budget notes
- Each post with a link costs ~$0.20 on X's pay-per-use pricing → your $5
  covers ~25 posts. To stretch it further, open `recall_bot.py` and set
  `INCLUDE_LINK = False` (drops cost to ~$0.015/post, ~300 posts on $5).
- `MAX_POSTS_PER_RUN = 5` is a safety cap so a busy recall day can't drain
  your balance in one run. Adjust if needed.
- Watch your balance in the X Developer Console and top up if/when this is
  clearly working.
