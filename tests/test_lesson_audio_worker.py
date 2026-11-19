import queue
import threading
import unittest
from unittest.mock import Mock

from studio.lesson_audio import AudioChecks


class AudioWorkerTests(unittest.TestCase):
    def worker(self, check, save=None):
        audio=AudioChecks.__new__(AudioChecks)
        audio.lock=threading.RLock();audio.jobs=queue.Queue();audio.queued=set()
        audio.closed=threading.Event();audio.worker_error=None
        audio._check=check;audio._save=save or Mock()
        audio.worker=threading.Thread(target=audio._loop,daemon=True)
        audio.worker.start()
        self.addCleanup(audio.close)
        return audio

    def enqueue(self, audio, tid):
        audio.queued.add(tid);audio.jobs.put({'id':tid,'path':'nonexistent-test-audio-file'})

    def test_failed_check_records_failure_and_processes_next_take(self):
        finished=threading.Event()
        def check(take):
            if take['id']=='first':raise OSError('temporary database error')
            finished.set()
        audio=self.worker(Mock(side_effect=check))
        self.enqueue(audio,'first');self.enqueue(audio,'second')
        self.assertTrue(finished.wait(2));audio.jobs.join()
        self.assertTrue(audio.worker.is_alive())
        tid,failure=audio._save.call_args.args
        self.assertEqual(tid,'first');self.assertEqual(failure['status'],'failed')
        self.assertFalse(audio.present(failure)['can_continue'])
        self.assertEqual(audio.worker_error['take_id'],'first')
        self.assertFalse(audio.queued)

    def test_storage_failure_does_not_kill_worker_or_retry_forever(self):
        finished=threading.Event()
        def check(take):
            if take['id']=='first':raise OSError('database unavailable')
            finished.set()
        audio=self.worker(Mock(side_effect=check),Mock(side_effect=OSError('still unavailable')))
        self.enqueue(audio,'first');self.enqueue(audio,'second')
        self.assertTrue(finished.wait(2));audio.jobs.join()
        self.assertEqual(audio._check.call_count,2)
        self.assertEqual(audio._save.call_count,1)
        self.assertTrue(audio.worker.is_alive())

    def test_shutdown_drains_pending_jobs_without_touching_files(self):
        started=threading.Event();release=threading.Event()
        def check(take):started.set();release.wait(2)
        audio=self.worker(Mock(side_effect=check))
        self.enqueue(audio,'first');self.assertTrue(started.wait(2))
        self.enqueue(audio,'second')
        closer=threading.Thread(target=audio.close);closer.start()
        self.assertTrue(audio.closed.wait(2));release.set();closer.join(2)
        self.assertFalse(closer.is_alive());self.assertFalse(audio.worker.is_alive())
        self.assertEqual(audio._check.call_count,1)
        self.assertEqual(audio.jobs.unfinished_tasks,0);self.assertFalse(audio.queued)
        audio.close()  # idempotent: no unconsumed second sentinel
        self.assertEqual(audio.jobs.unfinished_tasks,0)
        self.assertFalse(audio.ensure({'id':'after-close'},retry=True)['can_continue'])
        audio._save.assert_not_called()


if __name__=='__main__':unittest.main()
