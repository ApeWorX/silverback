from ape import chain
from ape.api import BlockAPI
from ape.types import ContractLog
from ape_tokens import tokens  # type: ignore[import]

from silverback import CircuitBreaker, SilverBackApp

# Do this to initialize your app
app = SilverBackApp()

# NOTE: Don't do any networking until after initializing app
try:
    USDC = tokens["USDC"]
except KeyError:
    # NOTE: For some reason, USDC is missing from many multi-chain tokenlists.
    if chain.provider.network.name == "mainnet":
        if chain.provider.network.ecosystem.name == "optimism":
            USDC = "0x7F5c764cBc14f9669B88837ca1490cCa17c31607"
        elif chain.provider.network.ecosystem.name == "arbitrum":
            USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

        else:
            raise  # The key error

    else:
        raise  # The key error


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
