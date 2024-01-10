from typing import Any, Optional, Tuple

from ape.logging import logger
from ape.types import ContractLog
from ape.utils import ManagerAccessMixin
from eth_utils.conversions import to_hex
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from silverback.persistence import HandlerResult
from silverback.types import SilverbackID, handler_id_block, handler_id_event
from silverback.utils import hexbytes_dict


def resolve_task(message: TaskiqMessage) -> Tuple[str, Optional[int], Optional[int]]:
    block_number = None
    log_index = None
    task_id = message.task_name

    if task_id == "block":
        block_number = message.args[0].number
        task_id = handler_id_block(block_number)
    elif "event" in task_id:
        block_number = message.args[0].block_number
        log_index = message.args[0].log_index
        # TODO: Should standardize on event signature here instead of name in case of overloading
        task_id = handler_id_event(message.args[0].contract_address, message.args[0].event_name)

    return task_id, block_number, log_index


class SilverbackMiddleware(TaskiqMiddleware, ManagerAccessMixin):
    def __init__(self, *args, **kwargs):
        def compute_block_time() -> int:
            genesis = self.chain_manager.blocks[0]
            head = self.chain_manager.blocks.head

            if not head.number or head.number == 0:
                return 10

            return int((head.timestamp - genesis.timestamp) / head.number)

        settings = kwargs.pop("silverback_settings")

        self.block_time = self.chain_manager.provider.network.block_time or compute_block_time()
        self.ident = SilverbackID.from_settings(settings)
        self.persistence = settings.get_persistent_store()

    def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        # TODO: Necessary because bytes/HexBytes doesn't encode/deocde well for some reason
        def fix_dict(data: dict, recurse_count: int = 0) -> dict:
            fixed_data: dict[str, Any] = {}
            for name, value in data.items():
                if isinstance(value, bytes):
                    fixed_data[name] = to_hex(value)
                elif isinstance(value, dict):
                    if recurse_count > 3:
                        raise RecursionError("Event object is too deep")
                    fixed_data[name] = fix_dict(value, recurse_count + 1)
                else:
                    fixed_data[name] = value

            return fixed_data

        message.args = [(fix_dict(arg) if isinstance(arg, dict) else arg) for arg in message.args]

        return message

    def _create_label(self, message: TaskiqMessage) -> str:
        if message.task_name == "block":
            args = f"[block={message.args[0].hash.hex()}]"

        elif "event" in message.task_name:
            args = f"[txn={message.args[0].transaction_hash},log_index={message.args[0].log_index}]"

        else:
            args = ""

        return f"{message.task_name}{args}"

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        if message.task_name == "block":
            # NOTE: Necessary because we don't know the exact block class
            message.args[0] = self.provider.network.ecosystem.decode_block(
                hexbytes_dict(message.args[0])
            )

        elif "event" in message.task_name:
            # NOTE: Just in case the user doesn't specify type as `ContractLog`
            message.args[0] = ContractLog.model_validate(message.args[0])

        logger.info(f"{self._create_label(message)} - Started")
        return message

    def post_execute(self, message: TaskiqMessage, result: TaskiqResult):
        percentage_time = 100 * (result.execution_time / self.block_time)
        logger.info(
            f"{self._create_label(message)} "
            f"- {result.execution_time:.3f}s ({percentage_time:.1f}%)"
        )

    async def post_save(self, message: TaskiqMessage, result: TaskiqResult):
        if not self.persistence:
            return

        handler_id, block_number, log_index = resolve_task(message)

        handler_result = HandlerResult.from_taskiq(
            self.ident, handler_id, block_number, log_index, result
        )

        try:
            await self.persistence.add_result(handler_result)
        except Exception as err:
            logger.error(f"Error storing result: {err}")

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult,
        exception: BaseException,
    ):
        logger.error(f"{message.task_name} - {type(exception).__name__}: {exception}")
