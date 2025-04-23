from collections import defaultdict

from ape.logging import logger
from ape.utils import ManagerAccessMixin
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from silverback.types import TaskType

IGNORE_LABELS: dict[TaskType, tuple[str, ...]] = defaultdict(tuple)
IGNORE_LABELS[TaskType.EVENT_LOG] = ("event", "address", "topics")
IGNORE_LABELS[TaskType.CRON_JOB] = ("cron",)


class SilverbackMiddleware(TaskiqMiddleware, ManagerAccessMixin):
    def __init__(self, *args, **kwargs):
        def compute_block_time() -> int:
            genesis = self.chain_manager.blocks[0]
            head = self.chain_manager.blocks.head

            if not head.number or head.number == 0:
                return 10

            return int((head.timestamp - genesis.timestamp) / head.number)

        self.block_time = self.chain_manager.provider.network.block_time or compute_block_time()

    def _create_label(self, message: TaskiqMessage, task_type: TaskType) -> str:
        if labels_str := ",".join(
            f"{k}={v}"
            for k, v in message.labels.items()
            # NOTE: Skip labels used for task processing
            if k not in ("task_type", *IGNORE_LABELS[task_type])
        ):
            return f"{message.task_name}[{labels_str}]"

        else:
            return message.task_name

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        if not (task_type_str := message.labels.get("task_type")):
            # NOTE: Not a Silverback task unless it has this
            return message

        task_type = TaskType(task_type_str)

        # Add labels for task logging based on task type
        # NOTE: We choose labels that will help us track down offending data items
        #       *without* fully doxxing more of _how_ the user is handling their data
        if task_type is TaskType.NEW_BLOCK:
            block = message.args[0]
            message.labels["block"] = block["hash"]

        elif task_type is TaskType.EVENT_LOG:
            log = message.args[0]
            # NOTE: One of these two should exist as keys in `log`
            message.labels["txn"] = log.get("transactionHash", log.get("transaction_hash"))
            message.labels["idx"] = log.get("logIndex", log.get("log_index"))

        elif task_type is TaskType.CRON_JOB:
            message.labels["time"] = str(message.args[0])

        msg = f"{self._create_label(message, task_type)} - Started"
        if message.task_name.startswith("system:"):
            logger.debug(msg)
        else:
            logger.info(msg)

        return message

    def post_execute(self, message: TaskiqMessage, result: TaskiqResult):
        if not (task_type_str := message.labels.get("task_type")):
            # NOTE: Not a Silverback task unless it has this
            return

        task_type = TaskType(task_type_str)

        if self.block_time:
            percentage_time = 100 * (result.execution_time / self.block_time)
            percent_display = f" ({percentage_time:.1f}%)"

        else:
            percent_display = ""

        msg = (
            f"{self._create_label(message, task_type)} "
            f"- {result.execution_time:.3f}s{percent_display}"
        )
        if result.is_err:
            logger.error(msg)
        elif message.task_name.startswith("system:"):
            logger.debug(msg)
        else:
            logger.success(msg)

    # NOTE: Unless stdout is ignored, error traceback appears in stdout, no need for `on_error`
