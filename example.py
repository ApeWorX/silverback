from ape import chain
from ape.api import BlockAPI
from ape.types import ContractLog
from ape_tokens import tokens  # type: ignore[import]

from silverback import CircuitBreaker, SilverbackApp

# Do this to initialize your app
app = SilverbackApp()

# NOTE: Don't do any networking until after initializing app
USDC = tokens["USDC"]
YFI = tokens["YFI"]


# Can handle some stuff on startup, like loading a heavy model or something
@app.on_startup()
def startup(state):
    return {"message": "Starting..."}


# This is how we trigger off of new blocks
@app.on_(chain.blocks)
def exec_block(block: BlockAPI):
    return len(block.transactions)


# This is how we trigger off of events
# Set new_block_timeout to adjust the expected block time.
@app.on_(USDC.Transfer, start_block=17793100, new_block_timeout=25)
# NOTE: Typing isn't required
def exec_event1(log):
    if log.log_index % 7 == 3:
        # If you ever want the app to shutdown under some scenario, call this exception
        raise CircuitBreaker("Oopsie!")
    return log.amount


@app.on_(YFI.Approval)
# Any handler function can be async too
async def exec_event2(log: ContractLog):
    return log.value


# Just in case you need to release some resources or something
@app.on_shutdown()
def shutdown(state):
    return {"message": "Stopping..."}
