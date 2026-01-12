"""Unit tests for SCA product lifecycle module."""

import mock


def test_product_start_when_both_flags_enabled():
    """Test that start() enables SCA detection when both flags are True."""
    with (
        mock.patch("ddtrace.internal.sca.product.tracer_config") as mock_tracer_config,
        mock.patch("ddtrace.internal.sca.product.asm_config") as mock_asm_config,
        mock.patch("ddtrace.appsec.sca.enable_sca_detection") as mock_enable,
    ):
        # Both flags enabled
        mock_tracer_config._sca_enabled = True
        mock_asm_config._sca_detection_enabled = True

        from ddtrace.internal.sca import product

        product.start()

        # Should call enable_sca_detection
        mock_enable.assert_called_once()


def test_product_start_when_sca_disabled():
    """Test that start() doesn't enable SCA detection when DD_APPSEC_SCA_ENABLED is False."""
    with (
        mock.patch("ddtrace.internal.sca.product.tracer_config") as mock_tracer_config,
        mock.patch("ddtrace.internal.sca.product.asm_config") as mock_asm_config,
        mock.patch("ddtrace.appsec.sca.enable_sca_detection") as mock_enable,
    ):
        # Main SCA flag disabled
        mock_tracer_config._sca_enabled = False
        mock_asm_config._sca_detection_enabled = True

        from ddtrace.internal.sca import product

        product.start()

        # Should not call enable_sca_detection
        mock_enable.assert_not_called()


def test_product_start_when_detection_disabled():
    """Test that start() doesn't enable SCA detection when DD_SCA_DETECTION_ENABLED is False."""
    with (
        mock.patch("ddtrace.internal.sca.product.tracer_config") as mock_tracer_config,
        mock.patch("ddtrace.internal.sca.product.asm_config") as mock_asm_config,
        mock.patch("ddtrace.appsec.sca.enable_sca_detection") as mock_enable,
    ):
        # Detection flag disabled
        mock_tracer_config._sca_enabled = True
        mock_asm_config._sca_detection_enabled = False

        from ddtrace.internal.sca import product

        product.start()

        # Should not call enable_sca_detection
        mock_enable.assert_not_called()


def test_product_start_when_both_flags_disabled():
    """Test that start() doesn't enable SCA detection when both flags are False."""
    with (
        mock.patch("ddtrace.internal.sca.product.tracer_config") as mock_tracer_config,
        mock.patch("ddtrace.internal.sca.product.asm_config") as mock_asm_config,
        mock.patch("ddtrace.appsec.sca.enable_sca_detection") as mock_enable,
    ):
        # Both flags disabled
        mock_tracer_config._sca_enabled = False
        mock_asm_config._sca_detection_enabled = False

        from ddtrace.internal.sca import product

        product.start()

        # Should not call enable_sca_detection
        mock_enable.assert_not_called()


def test_product_start_handles_exceptions():
    """Test that start() handles exceptions gracefully."""
    with (
        mock.patch("ddtrace.internal.sca.product.tracer_config") as mock_tracer_config,
        mock.patch("ddtrace.internal.sca.product.asm_config") as mock_asm_config,
        mock.patch("ddtrace.appsec.sca.enable_sca_detection") as mock_enable,
    ):
        # Both flags enabled
        mock_tracer_config._sca_enabled = True
        mock_asm_config._sca_detection_enabled = True
        # Make enable_sca_detection raise an exception
        mock_enable.side_effect = RuntimeError("Test error")

        from ddtrace.internal.sca import product

        # Should not raise - exception should be caught and logged
        product.start()


def test_product_stop():
    """Test that stop() calls disable_sca_detection()."""
    with mock.patch("ddtrace.appsec.sca.disable_sca_detection") as mock_disable:
        from ddtrace.internal.sca import product

        product.stop()

        # Should call disable_sca_detection
        mock_disable.assert_called_once()


def test_product_stop_with_join():
    """Test that stop() can be called with join parameter."""
    with mock.patch("ddtrace.appsec.sca.disable_sca_detection") as mock_disable:
        from ddtrace.internal.sca import product

        product.stop(join=True)

        # Should call disable_sca_detection
        mock_disable.assert_called_once()


def test_product_stop_handles_exceptions():
    """Test that stop() handles exceptions gracefully."""
    with mock.patch("ddtrace.appsec.sca.disable_sca_detection") as mock_disable:
        # Make disable_sca_detection raise an exception
        mock_disable.side_effect = RuntimeError("Test error")

        from ddtrace.internal.sca import product

        # Should not raise - exception should be caught and logged
        product.stop()


def test_product_restart():
    """Test that restart() calls stop() then start()."""
    with (
        mock.patch("ddtrace.internal.sca.product.tracer_config") as mock_tracer_config,
        mock.patch("ddtrace.internal.sca.product.asm_config") as mock_asm_config,
        mock.patch("ddtrace.appsec.sca.enable_sca_detection") as mock_enable,
        mock.patch("ddtrace.appsec.sca.disable_sca_detection") as mock_disable,
    ):
        # Both flags enabled
        mock_tracer_config._sca_enabled = True
        mock_asm_config._sca_detection_enabled = True

        from ddtrace.internal.sca import product

        product.restart()

        # Should call both disable and enable
        mock_disable.assert_called_once()
        mock_enable.assert_called_once()


def test_product_has_requires():
    """Test that the product module declares its dependencies."""
    from ddtrace.internal.sca import product

    assert hasattr(product, "requires")
    assert "remote-configuration" in product.requires
