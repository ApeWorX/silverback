from typing import Optional, Tuple

from ape.logging import logger
from ape.types import ContractLog
from ape.utils import ManagerAccessMixin
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from silverback.persistence import HandlerResult
from silverback.types import SilverbackIdent, handler_id_block, handler_id_event
from silverback.utils import hexbytes_dict


def resolve_task(message: TaskiqMessage) -> Tuple[str, Optional[int], Optional[int]]:
    block_number = None
    log_index = None
    task_id = message.task_name

    if task_id == "block":
        block_number = message.args[0].number
        task_id = handler_id_block(block_number)
    elif task_id == "event":
        block_number = message.args[0].block_number
        log_index = message.args[0].log_index
        task_id = handler_id_event(message.args[0].address, message.args[0].abi.name)

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
        self.ident = SilverbackIdent.from_settings(settings)
        self.persistence = settings.get_persistent_store()

    def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        # TODO: Necessary until https://github.com/ApeWorX/ape/issues/1465 is resolved

        def fix_dict(data: dict) -> dict:
            fixed_data = {}
            for name, value in data.items():
                if isinstance(value, bytes):
                    fixed_data[name] = value.hex()
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
            message.args[0] = ContractLog.parse_obj(message.args[0])

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
