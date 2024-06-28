#  Copyright 2022 Google LLC
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
from __future__ import annotations

from collections.abc import Callable
import time
import uuid

from flaky import flaky

import google.auth
from google.cloud import batch_v1
from google.cloud import resourcemanager_v3
import pytest

from ..create.create_with_container_no_mounting import create_container_job
from ..create.create_with_gpu_no_mounting import create_gpu_job
from ..create.create_with_script_no_mounting import create_script_job
from ..create.create_with_service_account import create_with_custom_service_account_job
from ..create.create_with_ssd import create_local_ssd_job

from ..delete.delete_job import delete_job
from ..get.get_job import get_job
from ..get.get_task import get_task
from ..list.list_jobs import list_jobs
from ..list.list_tasks import list_tasks
from ..logs.read_job_logs import print_job_logs

PROJECT = google.auth.default()[1]
REGION = "europe-central2"
ZONE = "europe-central2-b"
TIMEOUT = 600  # 10 minutes

WAIT_STATES = {
    batch_v1.JobStatus.State.STATE_UNSPECIFIED,
    batch_v1.JobStatus.State.QUEUED,
    batch_v1.JobStatus.State.RUNNING,
    batch_v1.JobStatus.State.SCHEDULED,
    batch_v1.JobStatus.State.DELETION_IN_PROGRESS,
}


@pytest.fixture
def job_name():
    return f"test-job-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def service_account() -> str:
    client = resourcemanager_v3.ProjectsClient()
    request = resourcemanager_v3.GetProjectRequest()
    request.name = f"projects/{PROJECT}"
    project = client.get_project(request)
    project_number = project.name.split("/")[-1]
    return f"{project_number}-compute@developer.gserviceaccount.com"


@pytest.fixture
def disk_name():
    return f"test-ssd-{uuid.uuid4().hex[:10]}"


def _test_body(test_job: batch_v1.Job, additional_test: Callable = None, region=REGION):
    start_time = time.time()
    try:
        while test_job.status.state in WAIT_STATES:
            if time.time() - start_time > TIMEOUT:
                pytest.fail("Timed out while waiting for job to complete!")
            test_job = get_job(
                PROJECT, region, test_job.name.rsplit("/", maxsplit=1)[1]
            )
            time.sleep(5)

        assert test_job.status.state == batch_v1.JobStatus.State.SUCCEEDED

        for job in list_jobs(PROJECT, region):
            if test_job.uid == job.uid:
                break
        else:
            pytest.fail(f"Couldn't find job {test_job.uid} on the list of jobs.")

        if additional_test:
            additional_test()
    finally:
        delete_job(PROJECT, region, test_job.name.rsplit("/", maxsplit=1)[1]).result()

    for job in list_jobs(PROJECT, region):
        if job.uid == test_job.uid:
            pytest.fail("The test job should be deleted at this point!")


def _check_tasks(job_name):
    tasks = list_tasks(PROJECT, REGION, job_name, "group0")
    assert len(list(tasks)) == 4
    for i in range(4):
        assert get_task(PROJECT, REGION, job_name, "group0", i) is not None
    print("Tasks tested")


def _check_logs(job, capsys):
    print_job_logs(PROJECT, job)
    output = [
        line
        for line in capsys.readouterr().out.splitlines(keepends=False)
        if line != ""
    ]
    assert len(output) == 4
    assert all("Hello world!" in log_msg for log_msg in output)


def _check_service_account(job: batch_v1.Job, service_account_email: str):
    assert job.allocation_policy.service_account.email == service_account_email


@flaky(max_runs=3, min_passes=1)
def test_script_job(job_name, capsys):
    job = create_script_job(PROJECT, REGION, job_name)
    _test_body(job, additional_test=lambda: _check_logs(job, capsys))


@flaky(max_runs=3, min_passes=1)
def test_container_job(job_name):
    job = create_container_job(PROJECT, REGION, job_name)
    _test_body(job, additional_test=lambda: _check_tasks(job_name))


@flaky(max_runs=3, min_passes=1)
def test_create_gpu_job(job_name):
    job = create_gpu_job(PROJECT, REGION, ZONE, job_name)
    _test_body(job, additional_test=lambda: _check_tasks)


@flaky(max_runs=3, min_passes=1)
def test_service_account_job(job_name, service_account):
    job = create_with_custom_service_account_job(
        PROJECT, REGION, job_name, service_account
    )
    _test_body(
        job, additional_test=lambda: _check_service_account(job, service_account)
    )


@flaky(max_runs=3, min_passes=1)
def test_ssd_job(job_name: str, disk_name: str, capsys: "pytest.CaptureFixture[str]"):
    job = create_local_ssd_job(PROJECT, REGION, job_name, disk_name)
    _test_body(job, additional_test=lambda: _check_logs(job, capsys))
