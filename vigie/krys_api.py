"""Routes locales sous le même contrôle CSRF que les écritures de la démo."""
from collections import deque
import threading

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from vigie.krys import Chat, HumanActions
from vigie.krys_mistral import Rephraser


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=1000)


class Preparation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str = Field(max_length=32)
    target: str = Field(max_length=128)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action_id: str = Field(max_length=128)
    confirmation: str = Field(max_length=16)


def attach_krys(app, knowledge, actions=None, *, rephraser=None):
    app.state.details_knowledge = knowledge
    from vigie.console.mails import attach_mailbox
    attach_mailbox(app, knowledge)
    audit = deque(maxlen=128)
    lock = threading.Lock()
    renderer = rephraser if rephraser is not None else Rephraser.from_env()

    def act(method, *args):
        with lock:
            book = knowledge()
            broker = actions if actions is not None else HumanActions(book)
            broker.book = book
            try:
                return getattr(broker, method)(*args)
            except PermissionError as error:
                raise HTTPException(403, str(error)) from error
            except ValueError as error:
                raise HTTPException(409, str(error)) from error

    def local_answer(question):
        with lock:
            return Chat(knowledge(), audit=audit).ask(question)

    @app.post('/demo/krys/ask')
    async def ask(body: Question):
        answer = await run_in_threadpool(local_answer, body.question)
        return await renderer.apply(answer)

    @app.post('/demo/krys/actions/prepare')
    def prepare(body: Preparation):
        return act('prepare', body.action, body.target)

    @app.post('/demo/krys/actions/confirm')
    def confirm(body: Confirmation):
        return act('confirm', body.action_id, body.confirmation)

    @app.get('/demo/krys.js')
    def script():
        return FileResponse(Path(__file__).with_name('krys_chat.js'), media_type='text/javascript')

    @app.get('/demo/page')
    def page():
        return FileResponse(Path(__file__).with_name('demo_page.html'), media_type='text/html')
