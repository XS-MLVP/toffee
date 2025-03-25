.. Toffee documentation master file, created by
   sphinx-quickstart on Fri Mar 14 11:59:30 2025.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Toffee documentation
====================

**Toffee** 是使用 Python 语言编写的一套硬件验证框架，它依赖于多语言转换工具 `Picker <https://github.com/XS-MLVP/picker>`_ ，该工具能够将硬件设计的 Verilog 代码转换为 Python Package，使得用户可以使用 Python 来驱动并验证硬件设计。

其吸收了部分 UVM 验证方法学，以保证验证环境的规范性和可复用性，并重新设计了整套验证环境的搭建方式，使其更符合软件领域开发者的使用习惯，从而使软件开发者可以轻易地上手硬件验证工作。

.. admonition:: v0.3.0 版本注意事项

   本次更新有几处 API 发生变动，可能会导致之前的代码无法正常运行，如果需要从旧版本更新至新版本，需要针对以下几处进行修改：

   - **修复了** `Executor` **可能会导致延迟一周期的问题。如果使用了** `Executor` **，需要检查是否时序受到影响**
   - `MonitorPort` **取消了自动比较机制。调用** `MonitorPort` **从输出改为了获取** `monitor_method` **的监测值。**

      因此原来的代码需要从::

         await self.monitor_port(item)

      修改为::

         assert await self.monitor_port() == item

   - `agent_hook` **目前可以独立于** `driver_hook` **使用。因此如果有两者同时使用的情况，需要修改其优先级。**
   - **如果使用了** `toffee.run` **，需要参照当前文档，提前初始化验证环境。**


.. toctree::
   :maxdepth: 1
   :caption: 快速开始

   quick-start


.. toctree::
   :maxdepth: 1
   :caption: 环境规范

   canonical_env

.. toctree::
   :maxdepth: 1
   :caption: 验证环境

   env/index
   env/start_test
   env/bundle
   env/agent
   env/build_env
   env/ref_model

.. toctree::
   :maxdepth: 1
   :caption: 测试用例

   cases/index
   cases/executor
   cases/pytest
   cases/cov

.. toctree::
   :maxdepth: 1
   :caption: 验证任务

   verification

.. toctree::
   :maxdepth: 2
   :caption: API 文档

   api/toffee
