from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.scheduler.runner import poll_due_jobs

POLL_INTERVAL_SECONDS = 60


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_due_jobs, "interval", seconds=POLL_INTERVAL_SECONDS)
    scheduler.start()
    return scheduler
