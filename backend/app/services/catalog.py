from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category

CATEGORY_TREE = {
    "后端开发": {
        "Java": {},
        "Go": {},
        "Python": {},
        "数据库": {"MySQL": {}, "PostgreSQL": {}, "MongoDB": {}},
        "缓存": {"Redis": {}, "Memcached": {}},
        "消息队列": {"Kafka": {}, "RabbitMQ": {}},
        "微服务/分布式": {"分布式事务": {}, "一致性协议": {}, "服务治理": {}},
        "并发与多线程": {},
    },
    "前端开发": {"JavaScript/TypeScript": {}, "React": {}, "Vue": {}, "性能优化": {}},
    "测试相关": {"单元测试": {}, "接口测试": {}, "性能测试": {}, "自动化测试": {}},
    "架构设计": {"系统设计题": {}, "场景题": {}},
}


async def seed_categories(session: AsyncSession) -> None:
    if await session.scalar(select(Category.id).limit(1)):
        return

    async def add_nodes(tree: dict, parent_id: int | None = None, parent_path: str = "") -> None:
        for index, (name, children) in enumerate(tree.items()):
            path = f"{parent_path}/{name}" if parent_path else name
            row = Category(name=name, path=path, parent_id=parent_id, sort_order=index)
            session.add(row)
            await session.flush()
            await add_nodes(children, row.id, path)

    await add_nodes(CATEGORY_TREE)
    await session.commit()
