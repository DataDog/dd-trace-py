//! `ConvertToPyO3Enum` derive.
//!
//! libdatadog does not allow putting PyO3 directly on its enums, but we can add strum to them.
//! This code creates PyO3 compatible structs with __int__ and __str__ to expose enum contents to python.

use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, Data, DeriveInput, Fields};

/// Derive a PyO3 wrapper enum from a newtype around an enum.
#[proc_macro_derive(ConvertToPyO3Enum)]
pub fn convert_to_pyo3_enum(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let wrapper = input.ident.clone();

    // The "name of the enum" is the wrapper's single unnamed field type.
    let inner = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Unnamed(fields) if fields.unnamed.len() == 1 => fields.unnamed[0].ty.clone(),
            _ => {
                return compile_error(
                    &input.ident,
                    "ConvertToPyO3Enum requires a newtype struct, e.g. `struct W(InnerEnum);`",
                )
            }
        },
        _ => {
            return compile_error(
                &input.ident,
                "ConvertToPyO3Enum can only be derived for a newtype struct",
            )
        }
    };

    let expanded = quote! {
        impl ::core::convert::From<#inner> for #wrapper {
            #[inline]
            fn from(value: #inner) -> Self {
                #wrapper(value)
            }
        }

        impl ::core::convert::From<#wrapper> for #inner {
            #[inline]
            fn from(value: #wrapper) -> Self {
                value.0
            }
        }

        #[::pyo3::pymethods]
        impl #wrapper {
            /// The canonical name of the variant (the inner enum's `Display`).
            fn __str__(&self) -> ::std::string::String {
                ::std::string::ToString::to_string(&self.0)
            }

            /// The variant's discriminant (e.g. a capability's bit position),
            /// matching what the old `#[pyclass(eq_int)]` enum exposed.
            fn __int__(&self) -> i64 {
                self.0 as i64
            }

            /// `ClassName.Variant`, e.g. `RemoteConfigProduct.AsmFeatures`. The
            /// class name is read at runtime because `#[pyclass]` consumes its
            /// own `name = "..."` attribute before this derive can see it.
            fn __repr__(slf: &::pyo3::Bound<'_, Self>) -> ::pyo3::PyResult<::std::string::String> {
                use ::pyo3::prelude::*;
                let variant: &'static str = ::core::convert::Into::into(slf.borrow().0);
                let class = slf.as_any().get_type().name()?;
                ::core::result::Result::Ok(::std::format!("{}.{}", class, variant))
            }
        }

        impl #wrapper {
            /// Register the wrapper class on `module` and add one class attribute
            /// per variant (named via `strum::IntoStaticStr`), reconstructing the
            /// enum on the Python side. Call this from the module init instead of
            /// `module.add_class`.
            pub fn register(
                module: &::pyo3::Bound<'_, ::pyo3::types::PyModule>,
            ) -> ::pyo3::PyResult<()> {
                use ::pyo3::prelude::*;
                use ::strum::IntoEnumIterator;

                module.add_class::<#wrapper>()?;
                let py = module.py();
                let cls = py.get_type::<#wrapper>();
                for variant in <#inner as ::strum::IntoEnumIterator>::iter() {
                    let name: &'static str = ::core::convert::Into::into(variant);
                    cls.as_any().setattr(name, #wrapper(variant))?;
                }
                ::core::result::Result::Ok(())
            }
        }
    };

    expanded.into()
}

fn compile_error(ident: &syn::Ident, msg: &str) -> TokenStream {
    syn::Error::new(ident.span(), msg).to_compile_error().into()
}
